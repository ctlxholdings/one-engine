"""Measurement rulebook (module card 10): the asset register.

The register's arithmetic (tranches, charges, disposals) is
:class:`one_engine.measurement.Register`; this rulebook moves cost into
service and posts what the register measures. Moved from :mod:`one_engine.packs.pf` on 2026-10-14 (Ali chose to move the rulebooks three or more
archetypes declare out of the packs; ``one_engine/archetypes.yaml``).

Rulebooks
---------
``asset_register``
    At each in-service period (COD, a major maintenance), CWIP moves to
    fixed assets and becomes a tranche of the register; each period's
    charge and the disposal at ``dispose_at`` come from
    :class:`~one_engine.measurement.Register`. ``first_charge`` is
    required. An optional ``tax`` register depreciates the same tranches
    over the tax life. With ``weights`` (one per period) in place of
    ``life_periods`` (stated null), each tranche is spread over the rest of
    the weights (``Policy.weights``: ModelOff's days of operation left,
    step 7h). Fixed assets on the opening balance sheet are a tranche from
    the first in-service period. ``dispose_when`` (default ``end``) is
    ``start`` for an asset retired at the start of its disposal period
    (an oven replaced at the start of the year: no charge that year, the
    golds' ``bakery_disposal``). Either register may instead take a
    ``declining_rate`` (``Policy.declining_rate``: ModelOff 2012's 40% tax
    depreciation, step 7i); exactly one of the three is given.
    ``residual`` (default 0) is the residual value of the cost placed in
    service at the first in-service period (IAS 16.53: a plant's salvage,
    the golds' ``arise``): the books depreciate that cost less the
    residual, which stays in the asset account until the disposal writes
    it off. A ``tax`` register depreciates the full cost.
    ``carried_accumulated`` (default 0; the golds' ``isp_levy``) is the
    depreciation already charged on the fixed assets carried in, before the
    first period: the books carry them at net book value, and the gross view
    below shows their cost as that value plus it, and their accumulated
    depreciation from it. The books and the charge do not change.

    Each period the register records, under the rulebook's own name, its
    gross view (the golds report cost and accumulated depreciation apart):
    ``cost`` placed in service and ``accumulated`` depreciation, both
    closing and both cleared by a disposal; the period's ``charge``; the
    ``disposed_cost``, ``disposed_accumulated`` and ``disposed_nbv`` taken
    out by a disposal; and, with a ``tax`` register, its ``tax_charge``,
    closing ``tax_nbv`` (the tax written-down value) and the
    ``tax_disposed_nbv`` a disposal deducts.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Mapping

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
from one_engine.measurement import Policy, Register
from one_engine.units import exact


class AssetRegisters:
    """A rulebook's book register and, if declared, the tax authority's register of the same assets.

    Shared through ``ctx["registers"]`` under the rulebook's name, so the
    income tax rulebook can replace book depreciation by tax depreciation
    and measure the temporary difference.
    """

    def __init__(self, book: Register, tax, expense: str, disposal: str):
        self.book, self.tax, self.expense, self.disposal = book, tax, expense, disposal
        self.tax_moves: Dict[int, Fraction] = {}       # period -> tax depreciation + tax disposal


def _policy(r: Reader, where: str) -> Policy:
    """A depreciation policy: ``life_periods`` (stated, null when another basis is given), or ``weights``
    by period, or a ``declining_rate``; exactly one, and the first-charge convention."""
    life = r.take("life_periods")
    weights = r.take_or_default("weights", None, reason="no weights by period").value
    declining = r.take_or_default("declining_rate", None, reason="not declining balance").value
    if sum(x is not None for x in (life, weights, declining)) != 1:
        raise InputError(f"{where}: give life_periods, weights or declining_rate (life_periods: null), exactly one")
    return Policy(None if life is None else int(life), r.take("first_charge"), weights,
                  None if declining is None else exact(declining))


def asset_register(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, unit = r.take("entity"), r.take("phase"), ctx["currency"]
    src, fa, dep, loss = r.take("from"), r.take("to"), r.take("expense"), r.take("disposal")
    raw_in = r.take("in_service")
    in_service = [int(x) for x in raw_in] if isinstance(raw_in, list) else [int(raw_in)]
    dispose_at = int(r.take("dispose_at"))
    dispose_when = r.take_or_default("dispose_when", "end", reason="retired at the end of its period").value
    if dispose_when not in ("start", "end"):
        raise InputError(f"asset_register: dispose_when is start or end, not {dispose_when!r}")
    reg = Register(_policy(r, "asset_register"))
    residual = exact(r.take_or_default("residual", 0, reason="depreciated to nil").value)
    carried = exact(r.take_or_default("carried_accumulated", 0,
                                      reason="nothing was charged on the assets carried in before the first period").value)
    tax_raw = r.take_or_default("tax", None, reason="the tax authority depreciates as the books do").value
    tax_reg = None
    if tax_raw is not None:
        tr = Reader(tax_raw, source="asset_register.tax")
        tax_reg = Register(_policy(tr, "asset_register.tax"))
        tr.finish()
    regs = AssetRegisters(reg, tax_reg, dep, loss)
    ctx["registers"][ctx["name"]] = regs
    for name, nature in ((fa, Nature.ASSET), (dep, Nature.EXPENSE), (loss, Nature.EXPENSE)):
        group.declare(entity, name, nature, unit)

    name = ctx["name"]

    def post(g, t, debit, credit, amount, event):
        # every posting here raises a debit-natured account and lowers another; the event id carries the
        # rulebook's name, so two registers on one book (two asset classes, step 7p) do not collide
        if amount:
            g.post(entity, Posting(t, phase, debit, credit, amount, {debit: 1, credit: -1}, "measurement", f"{name}.{event}:{t}"))

    # the register's gross view, for reports that show cost and accumulated depreciation apart (the golds):
    # what was placed in service, and what has been charged on it, until a disposal clears both
    gross = {"cost": Fraction(0), "charged": Fraction(0), "tax_charged": Fraction(0)}

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        disposed = {"cost": Fraction(0), "charged": Fraction(0), "nbv": Fraction(0), "tax_nbv": Fraction(0)}

        def retire() -> None:
            disposed.update(cost=gross["cost"], charged=gross["charged"], nbv=gross["cost"] - gross["charged"],
                            tax_nbv=tax_reg.nbv() if tax_reg is not None else Fraction(0))
            gross.update(cost=Fraction(0), charged=Fraction(0))

        if t == in_service[0] and carried and not book.opening(fa, t):
            raise InputError(f"asset_register {name}: carried_accumulated is {carried}, but nothing is carried in")
        if t == in_service[0] and book.opening(fa, t):
            # the gross view of what is carried in: its cost is its book value plus what was charged on it before
            gross["cost"] += book.opening(fa, t) + carried
            gross["charged"] += carried
            # fixed assets carried in on an opening balance sheet are a tranche from the first in-service
            # period: otherwise the register would never depreciate them
            for x in (reg, tax_reg):
                if x is not None:
                    x.add(t, book.opening(fa, t))
        if t in in_service:
            cost = book.balance(src, t)
            post(g, t, fa, src, cost, "in_service")
            # the residual value is held back from the books' first tranche: it stays in the asset account,
            # never depreciated (IAS 16.53), until the disposal writes it off. Tax depreciates the full cost
            held = residual if t == in_service[0] else Fraction(0)
            if held > cost:
                raise InputError(f"asset_register: residual {held} exceeds the cost {cost} placed in service in {t}")
            reg.add(t, cost - held)
            gross["cost"] += cost
            if tax_reg is not None:
                tax_reg.add(t, cost)
        if t == dispose_at and dispose_when == "start":
            # retired before the period runs: written off at its opening book value, nothing charged after
            retire()
            post(g, t, loss, fa, reg.dispose() + residual, "disposal")
            if tax_reg is not None:
                regs.tax_moves[t] = tax_reg.dispose()
        charge = tax_charge = Fraction(0)
        if t >= in_service[0]:
            charge = reg.charge(t)
            gross["charged"] += charge
            post(g, t, dep, fa, charge, "depreciation")
            if tax_reg is not None:
                tax_charge = tax_reg.charge(t)
                regs.tax_moves[t] = regs.tax_moves.get(t, Fraction(0)) + tax_charge
        if t == dispose_at and dispose_when == "end":
            retire()
            post(g, t, loss, fa, reg.dispose() + residual, "disposal")
            if tax_reg is not None:
                regs.tax_moves[t] = regs.tax_moves.get(t, Fraction(0)) + tax_reg.dispose()
        g.record(name, t, cost=gross["cost"], accumulated=gross["charged"], charge=charge,
                 disposed_cost=disposed["cost"], disposed_accumulated=disposed["charged"], disposed_nbv=disposed["nbv"],
                 tax_charge=tax_charge, tax_nbv=tax_reg.nbv() if tax_reg is not None else None,
                 tax_disposed_nbv=disposed["tax_nbv"])

    group.register(phase, handler)


RULEBOOKS = {"asset_register": asset_register}
