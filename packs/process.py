"""Process network: an agri-industrial mass balance posted on the matter ledger.

What this pack is
-----------------
An asset generator for programmes built from processes: fields that harvest,
herds that eat, plants that press, mill and grade, stores and trucks that
lose a little of everything they carry. Baghara is the first case. Its
registry (``uof_process_registry.yaml``) and parameter dossier
(``uof_promoter_v2.yaml``) are read unchanged: each process names a driver
formula for its gross input, output streams as ratio formulas, an optional
crop-residue sub-balance, an optional physical hand-off for livestock (whose
dry-matter balance is not the animal or the milk that leaves), and twelve
monthly weights.

The pack computes the year the way the old engine does (same formulas, same
order, same scope rule), then posts every stream, month by month, as matter
moving between accounts in one programme book. That is the point: in the old
engine each process checks only that its own outputs add up to its input
("registry arithmetic"); here the kernel checks that every tonne a process
sends to another process is actually taken by it, that no stock goes below
zero in any month, and that each unit (wet tonnes, dry-matter tonnes)
balances against the declared boundaries.

Accounts (one book, entity ``programme``, 12 monthly periods, one phase)
------------------------------------------------------------------------
* ``P[u]`` (STOCK, floor 0): process ``P``'s throughput in unit ``u``. Its
  input arrives and its outputs leave in the same month, so it must be back
  at zero at each month end (:meth:`Run.unclosed` lists any that are not).
* ``P.stream`` / ``P.product.name`` (STOCK, floor 0): an output sent to
  another process. The consumer draws from it. A stock that is drawn before
  it is filled is carried in from the previous season: the pack computes the
  smallest opening inventory that keeps every month at or above zero
  (:attr:`Run.opening_inventory`). In a steady state the stock ends the
  year where it began; one that grows is matter nobody takes
  (:meth:`Run.stranded`).
* Boundaries (BOUNDARY) per unit: the registry's external nodes (supply,
  sales, atmosphere, disposal, fertility), ``convert`` for a dry-matter
  conversion, ``brought_forward`` for the opening inventory, and the named
  gaps the case declares.

How a consumer's draws are found
--------------------------------
From the consumer's own formula, never from a second declaration:

* a driver (or own-production formula) is a sum of terms; a term that names
  one upstream stream (``C1_peanut.net_grain * uof_count``) draws that
  term's value from that stream's account. A term naming two streams, or a
  subtracted stream, refuses;
* a term naming no stream comes from the input's ``source_node``, which must
  then be a boundary;
* a demand-driven input whose driver names no stream (a herd eating
  ``total_input_t``) draws from the reconciliation output addressed to its
  ``process_id``, the old engine's own pairing rule;
* a term whose stream is in another unit (wet oilcake fed as dry matter)
  needs a declared conversion: the case names the factor, the wet tonnes
  leave through ``convert[t]`` and the dry matter enters through
  ``convert[t_DM]``. The factor must multiply the term, which is checked.

Scope: a process at a per-unit location (Baghara's ``UOF``, one 100-ha farm)
is computed per unit, as in the old engine, and posted times the number of
units; hub, programme and market processes are posted as computed.

Nothing silent
--------------
* Numbers typed in the files are exact decimals; formulas are evaluated in
  exact arithmetic (only ``+ - * /``, numbers, names and stream references).
* A parameter path that does not resolve, or resolves to anything other than
  a number or a mapping with ``base`` or ``value``, refuses. The old engine
  returns 0.0 with a warning for the first and 0.0 silently for the second.
* A registry key this pack does not read refuses, unless it is one of the
  descriptive keys listed in ``_DESCRIPTIVE``.
* A livestock hand-off whose physical outputs exceed its physical inputs
  (live-weight gain, milk) does not conserve on its own. The case must name
  the gap that sources the difference; the posting carries the STAND_IN
  state. Without the name, the kernel's floor refuses the run.
* A feed deficit is bought from the registry's ``gap_handling.source`` and
  carries STAND_IN; a surplus with no stated destination refuses.
"""
from __future__ import annotations

import ast
import pathlib
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from one_engine.inputs import Reader, State, load_yaml_strict
from one_engine.kernel import Account, Book, Nature, Posting
from one_engine.units import exact

MONTHS = 12
PHASE = "operate"
TEMPLATES = ("production_split", "partition", "reconciliation")

# Registry keys that describe a process and change no quantity.
_DESCRIPTIVE = {"name", "chain", "phase", "logic", "note"}
# head_count is reporting only in the old engine (it feeds no stream).
_REPORTING = {"head_count"}
_PROCESS_KEYS = {"process_id", "location", "template", "basis", "seasonal_profile", "params", "derived",
                 "driver", "inputs", "outputs", "residue", "product_handoff", "own_production",
                 "gap_handling"} | _DESCRIPTIVE | _REPORTING


class ProcessError(ValueError):
    """A registry, a parameter or a case declaration cannot be read honestly."""


# ── exact formula evaluation ─────────────────────────────────────────────────

def _dotted(node: ast.AST) -> Optional[str]:
    """``A.b.c`` as text, or None if ``node`` is not a plain attribute chain."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and parts:
        return ".".join([node.id] + parts[::-1])
    return None


class Formula:
    """One registry formula, parsed once, evaluated exactly.

    ``text`` may be a number typed in YAML (then it is a constant). Numbers
    written inside a formula are read from their source text, so ``0.85``
    is 85/100.
    """

    def __init__(self, text: Any, where: str):
        self.where = where
        if not isinstance(text, str):
            self.text, self.tree = None, None
            self.constant = exact(text)
            return
        self.text, self.constant = text, None
        try:
            self.tree = ast.parse(text, mode="eval").body
        except SyntaxError as exc:
            raise ProcessError(f"{where}: cannot parse formula {text!r}") from exc

    def value(self, ns: Mapping[str, Fraction], node: ast.AST = None) -> Fraction:
        if self.constant is not None:
            return self.constant
        return self._eval(self.tree if node is None else node, ns)

    def terms(self) -> List[ast.AST]:
        """The top-level terms of a sum; refuses a subtracted term."""
        if self.tree is None:
            return []
        out, stack = [], [self.tree]
        while stack:
            n = stack.pop()
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                stack += [n.right, n.left]
            elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub) and self.refs(n.right):
                raise ProcessError(f"{self.where}: a stream is subtracted in {self.text!r}; a draw cannot be negative")
            else:
                out.append(n)
        return out

    @staticmethod
    def refs(node: ast.AST) -> List[str]:
        """Dotted names (stream references) inside ``node``, longest chains only."""
        found = {_dotted(n) for n in ast.walk(node) if isinstance(n, ast.Attribute)} - {None}
        return sorted(d for d in found if not any(o.startswith(d + ".") for o in found))

    def _eval(self, n: ast.AST, ns: Mapping[str, Fraction]) -> Fraction:
        if isinstance(n, ast.BinOp):
            a, b = self._eval(n.left, ns), self._eval(n.right, ns)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if isinstance(n.op, ast.Div):
                if b == 0:
                    raise ProcessError(f"{self.where}: division by zero in {self.text!r}")
                return a / b
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = self._eval(n.operand, ns)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return exact(ast.get_source_segment(self.text, n))
        name = n.id if isinstance(n, ast.Name) else _dotted(n)
        if name is not None:
            if name not in ns:
                raise ProcessError(f"{self.where}: '{name}' is not defined for {self.text!r}")
            return ns[name]
        raise ProcessError(f"{self.where}: {type(n).__name__} is not allowed in a formula ({self.text!r})")


def resolve(params: Mapping[str, Any], path: str, where: str) -> Fraction:
    """A parameter's base value at a dotted ``path`` in the dossier.

    A mapping gives its ``base`` (else its ``value``), as in the old engine;
    anything else that is not a number refuses.
    """
    node: Any = params
    for k in path.split("."):
        if not isinstance(node, Mapping) or k not in node:
            raise ProcessError(f"{where}: parameter path '{path}' does not resolve")
        node = node[k]
    if isinstance(node, Mapping):
        if "base" in node:
            node = node["base"]
        elif "value" in node:
            node = node["value"]
        else:
            raise ProcessError(f"{where}: '{path}' is a mapping with neither 'base' nor 'value'")
    try:
        return exact(node)
    except TypeError as exc:
        raise ProcessError(f"{where}: '{path}' is not a number ({node!r})") from exc


# ── the plan: one year, computed as the old engine does ──────────────────────

@dataclass(frozen=True)
class Leg:
    """One annual movement of matter, in programme scope, spread by ``weights``."""

    process: str
    event: str
    debit: str
    credit: str
    amount: Fraction
    unit: str
    state: State


@dataclass
class _Proc:
    key: str
    pid: str
    unit: str
    scale: Fraction
    weights: Tuple[Fraction, ...]
    legs: List[Leg]
    values: Dict[str, Fraction]          # stream name -> annual amount, process's own scope


class Network:
    """A registry and a parameter dossier, with the case's declarations.

    Parameters
    ----------
    registry, params:
        The two YAML documents, loaded with
        :func:`~one_engine.inputs.load_yaml_strict`.
    case:
        A :class:`~one_engine.inputs.Reader` over the case's ``network``
        block: ``units`` (basis -> unit code), ``per_unit_locations``,
        ``boundaries`` (the external node names), ``residue_source`` (where
        a crop's residue comes from; the registry does not say),
        ``conversions`` (``"P.input": factor_name``) and ``gaps``
        (``P: gap_name``) for hand-offs that do not conserve.
    """

    def __init__(self, registry: Mapping[str, Any], params: Mapping[str, Any], case: Reader):
        self.registry = registry
        self.params = params
        self.units: Dict[str, str] = dict(case.take("units"))
        self.per_unit = set(case.take("per_unit_locations"))
        self.boundaries = set(case.take("boundaries"))
        self.residue_source = case.take("residue_source")
        self.conversions: Dict[str, str] = dict(case.take("conversions"))
        self.gaps: Dict[str, str] = dict(case.take("gaps"))
        for key, proc in registry.items():
            unknown = set(proc) - _PROCESS_KEYS
            if unknown:
                raise ProcessError(f"{key}: registry keys not read by this pack: {sorted(unknown)}")
            if proc.get("template") not in TEMPLATES:
                raise ProcessError(f"{key}: template {proc.get('template')!r} is not built ({TEMPLATES})")
            if proc.get("basis") not in self.units:
                raise ProcessError(f"{key}: basis {proc.get('basis')!r} has no declared unit")
        for name in list(self.conversions) + list(self.gaps):
            if name.split(".")[0] not in registry:
                raise ProcessError(f"declared for '{name}', which is not a registry process")

    # ── helpers ─────────────────────────────────────────────────────────────

    def _unit(self, basis: str) -> str:
        return self.units[basis]

    @staticmethod
    def _bnd(node: str, unit: str) -> str:
        return f"{node}[{unit}]"

    @staticmethod
    def _proc_acct(key: str, unit: str) -> str:
        return f"{key}[{unit}]"

    def _weights(self, key: str, proc: Mapping[str, Any]) -> Tuple[Fraction, ...]:
        w = [exact(x) for x in (proc.get("seasonal_profile") or {}).get("weights") or []]
        if len(w) != MONTHS or sum(w) <= 0 or any(x < 0 for x in w):
            raise ProcessError(f"{key}: needs twelve non-negative monthly weights with a positive sum")
        total = sum(w)
        return tuple(x / total for x in w)

    def _local(self, key: str, proc: Mapping[str, Any]) -> Dict[str, Fraction]:
        ns = {name: resolve(self.params, path, f"{key}.params.{name}")
              for name, path in (proc.get("params") or {}).items()}
        for name, text in (proc.get("derived") or {}).items():       # in order; params only, as in the old engine
            ns[name] = Formula(text, f"{key}.derived.{name}").value(ns)
        return ns

    # ── the year ────────────────────────────────────────────────────────────

    def plan(self, n_units: int) -> "Plan":
        """Compute the year at ``n_units`` and list every leg to post."""
        if not isinstance(n_units, int) or n_units < 1:
            raise ProcessError(f"the number of units must be a positive integer, got {n_units!r}")
        glob: Dict[str, Fraction] = {"uof_count": Fraction(n_units)}
        stream_acct: Dict[str, Tuple[str, str]] = {}      # "P.s" -> (account, unit), for internal streams
        procs: Dict[str, _Proc] = {}
        demand: Dict[str, Fraction] = defaultdict(Fraction)
        recon_out: Dict[str, Tuple[str, str]] = {}        # process_id -> (account, unit) of the reconciliation output
        for key, proc in self.registry.items():
            if proc["template"] == "reconciliation":
                for o in proc["outputs"]:
                    recon_out[o["destination_node"]] = (f"{key}.{o['name']}", self._unit(proc["basis"]))

        order = [k for k, p in self.registry.items() if p["template"] != "reconciliation"] + \
                [k for k, p in self.registry.items() if p["template"] == "reconciliation"]
        for key in order:
            proc = self.registry[key]
            pid = proc["process_id"]
            unit = self._unit(proc["basis"])
            scale = Fraction(n_units) if proc["location"] in self.per_unit else Fraction(1)
            p = _Proc(key, pid if isinstance(pid, str) else "/".join(pid), unit, scale,
                      self._weights(key, proc), [], {})
            local = self._local(key, proc)
            ns = {**glob, **local}                                   # local wins, as in the old engine
            here = self._proc_acct(key, unit)

            def out(name: str, dest: str, amount: Fraction, u: str, prefix: str = "") -> None:
                p.values[prefix + name] = amount
                if dest not in self.boundaries:                      # a zero stream is still a stream
                    stream_acct[f"{key}.{prefix}{name}"] = (f"{key}.{prefix}{name}", u)
                if amount == 0:
                    return
                if dest in self.boundaries:
                    p.legs.append(Leg(key, prefix + name, self._bnd(dest, u), self._proc_acct(key, u),
                                      amount * scale, u, State.DERIVED))
                else:
                    acct = f"{key}.{prefix}{name}"
                    stream_acct[acct] = (acct, u)
                    p.legs.append(Leg(key, prefix + name, acct, self._proc_acct(key, u),
                                      amount * scale, u, State.DERIVED))

            if proc["template"] == "reconciliation":
                own = Fraction(0)
                for item in proc.get("own_production") or []:
                    f = Formula(item["formula"], f"{key}.own_production.{item['name']}")
                    amount = f.value(ns)
                    own += amount
                    p.values[item["name"]] = amount
                    self._draw(p, f, ns, item["name"], item["source_node"], amount, here, stream_acct, None)
                total = Fraction(0)
                for o in proc["outputs"]:
                    d = demand.get(o["destination_node"], Fraction(0))
                    total += d
                    out(o["name"], "", d, unit)
                gap = total - own
                if gap > 0:
                    src = (proc.get("gap_handling") or {}).get("source")
                    if src not in self.boundaries:
                        raise ProcessError(f"{key}: a deficit of {float(gap):,.6g} {unit} needs a gap_handling source "
                                           f"that is a boundary, got {src!r}")
                    p.values["other_feed_sources"] = gap
                    p.legs.append(Leg(key, "gap", here, self._bnd(src, unit), gap, unit, State.STAND_IN))
                elif gap < 0:
                    raise ProcessError(f"{key}: a surplus of {float(-gap):,.6g} {unit} has no stated destination")
            else:
                driver = Formula(proc["driver"], f"{key}.driver")
                gross = driver.value(ns)
                inp = proc["inputs"]
                if len(inp) != 1:
                    raise ProcessError(f"{key}: expected one input, got {len(inp)}")
                p.values[inp[0]["name"]] = gross
                self._draw(p, driver, ns, inp[0]["name"], inp[0]["source_node"], gross, here, stream_acct,
                           recon_out.get(p.pid))
                for o in proc["outputs"]:
                    r = Formula(o["ratio"], f"{key}.outputs.{o['name']}").value(ns)
                    out(o["name"], o["destination_node"], gross * r, unit)
                res = proc.get("residue")
                if res:
                    rd = Formula(res["driver"], f"{key}.residue.driver").value(ns)
                    if rd > 0:                                            # the old engine skips a zero residue
                        p.values["gross_residue"] = rd
                        p.legs.append(Leg(key, "gross_residue", here, self._bnd(self.residue_source, unit),
                                          rd * scale, unit, State.DERIVED))
                        for o in res["outputs"]:
                            r = Formula(o["ratio"], f"{key}.residue.{o['name']}").value(ns)
                            out(o["name"], o["destination_node"], rd * r, unit)
                ph = proc.get("product_handoff")
                if ph:
                    u = self._unit(ph["basis"])
                    hand = self._proc_acct(key, u)
                    balance = Fraction(0)
                    for i in ph.get("inputs") or []:
                        m = Formula(i["mass"], f"{key}.product_handoff.{i['name']}").value(ns)
                        p.values["product." + i["name"]] = m
                        if i["source_node"] not in self.boundaries:
                            raise ProcessError(f"{key}: hand-off input from '{i['source_node']}' is not a boundary")
                        if m:
                            p.legs.append(Leg(key, "product." + i["name"], hand, self._bnd(i["source_node"], u),
                                              m * scale, u, State.DERIVED))
                        balance += m
                    for o in ph.get("outputs") or []:
                        m = Formula(o["mass"], f"{key}.product_handoff.{o['name']}").value(ns)
                        out(o["name"], o["destination_node"], m, u, prefix="product.")
                        balance -= m
                    if balance != 0 and key in self.gaps:
                        g = self._bnd(self.gaps[key], u)
                        leg = (Leg(key, "gap", hand, g, -balance * scale, u, State.STAND_IN) if balance < 0 else
                               Leg(key, "gap", g, hand, balance * scale, u, State.STAND_IN))
                        p.legs.append(leg)
                        p.values["gap"] = -balance
                if proc["template"] == "partition":
                    demand[p.pid] += gross * scale

            for name, v in p.values.items():
                glob[f"{key}.{name}"] = v
            procs[key] = p
        return Plan(self, n_units, procs, stream_acct)

    def _draw(self, p: _Proc, f: Formula, ns, name, source, amount, here, stream_acct, paired) -> None:
        """Post the input ``name`` of ``p``: from a boundary, from named streams, or from its pair."""
        unit = p.unit
        terms = f.terms()
        refd = [t for t in terms if Formula.refs(t)]
        if not refd:
            if source in self.boundaries:
                if amount:
                    p.legs.append(Leg(p.key, name, here, self._bnd(source, unit), amount * p.scale, unit, State.DERIVED))
                return
            if paired is None:
                raise ProcessError(f"{p.key}.{name}: source '{source}' is not a boundary and the formula names no stream")
            acct, u = paired
            if u != unit:
                raise ProcessError(f"{p.key}.{name}: draws {u} from {acct} into {unit}")
            if amount:
                p.legs.append(Leg(p.key, name, here, acct, amount * p.scale, unit, State.DERIVED))
            return
        if len(refd) != len(terms):
            raise ProcessError(f"{p.key}.{name}: {f.text!r} mixes stream terms with terms from nowhere")
        for t in terms:
            r = Formula.refs(t)
            if len(r) != 1:
                raise ProcessError(f"{p.key}.{name}: a term of {f.text!r} names {len(r)} streams; it must name one")
            ref = r[0]
            if ref not in stream_acct:
                raise ProcessError(f"{p.key}.{name}: '{ref}' is not an internal stream computed before {p.key}")
            acct, u = stream_acct[ref]
            v = f.value(ns, t) * p.scale
            if v == 0:
                continue
            if v < 0:
                raise ProcessError(f"{p.key}.{name}: the draw on {ref} is negative ({float(v):,.6g})")
            if u == unit:
                p.legs.append(Leg(p.key, name, here, acct, v, unit, State.DERIVED))
                continue
            factor_name = self.conversions.get(f"{p.key}.{name}")
            if factor_name is None:
                raise ProcessError(f"{p.key}.{name}: draws {u} ({ref}) into {unit} with no declared conversion")
            factor = ns[factor_name]
            src = f.value({**ns, factor_name: Fraction(1)}, t) * p.scale
            if src * factor != v or factor <= 0:
                raise ProcessError(f"{p.key}.{name}: the conversion factor '{factor_name}' does not multiply the term")
            p.legs.append(Leg(p.key, name, self._bnd("convert", u), acct, src, u, State.DERIVED))
            p.legs.append(Leg(p.key, name, here, self._bnd("convert", unit), v, unit, State.DERIVED))


class Plan:
    """The year's legs, ready to post; built by :meth:`Network.plan`."""

    def __init__(self, net: Network, n_units: int, procs: Dict[str, _Proc], streams: Dict[str, Tuple[str, str]]):
        self.net, self.n_units, self.procs, self.streams = net, n_units, procs, streams

    def value(self, process: str, stream: str) -> Fraction:
        """An annual amount in the process's own scope (per unit for a farm), as the old engine reports it."""
        return self.procs[process].values[stream]

    def monthly(self) -> List[List[Tuple[Leg, Fraction]]]:
        """Each month's legs with their amounts (annual amount times the process's weight)."""
        out = []
        for m in range(MONTHS):
            out.append([(leg, leg.amount * p.weights[m]) for p in self.procs.values() for leg in p.legs
                        if leg.amount * p.weights[m] != 0])
        return out

    def accounts(self) -> List[Account]:
        """Every account a leg touches: process and stream stocks (floor 0), boundaries."""
        names: Dict[str, Tuple[Nature, str]] = {}
        for p in self.procs.values():
            for leg in p.legs:
                for a in (leg.debit, leg.credit):
                    is_proc = a.endswith("]") and a[:a.rindex("[")] in self.procs
                    names.setdefault(a, (Nature.STOCK if is_proc or a in self.streams else Nature.BOUNDARY, leg.unit))
        for u in {u for _, u in names.values()}:
            names.setdefault(f"brought_forward[{u}]", (Nature.BOUNDARY, u))
        return [Account(a, nat, u, floor=0 if nat is Nature.STOCK else None) for a, (nat, u) in names.items()]

    def opening_inventory(self) -> Dict[str, Fraction]:
        """The least stock each internal stream needs at the start of the year to stay at or above zero."""
        need: Dict[str, Fraction] = {}
        level: Dict[str, Fraction] = defaultdict(Fraction)
        for month in self.monthly():
            for leg, amt in month:
                if leg.debit in self.streams:
                    level[leg.debit] += amt
                if leg.credit in self.streams:
                    level[leg.credit] -= amt
            for a, v in level.items():
                need[a] = max(need.get(a, Fraction(0)), -v)
        return {a: v for a, v in need.items() if v > 0}

    def post(self, entity: str = "programme") -> "Run":
        """Open the book with the opening inventory and post the twelve months."""
        inv = self.opening_inventory()
        accounts = self.accounts()
        unit_of = {a.name: a.unit for a in accounts}
        opening: Dict[str, Fraction] = defaultdict(Fraction)
        for a, v in inv.items():
            opening[a] += v
            opening[f"brought_forward[{unit_of[a]}]"] += v
        book = Book(entity, MONTHS, (PHASE,), accounts, opening)
        nature = {a.name: a.nature for a in accounts}
        for m, month in enumerate(self.monthly()):
            for leg, amt in month:
                effect = {leg.debit: 1 if nature[leg.debit].debit_natured else -1,
                          leg.credit: -1 if nature[leg.credit].debit_natured else 1}
                book.post(Posting(m, PHASE, leg.debit, leg.credit, amt, effect, "process", f"{leg.process}.{leg.event}",
                                  leg.state))
            book.close_period(m)
        return Run(self, book, inv)


class Run:
    """A posted year: the book, its opening inventory, and the network checks."""

    def __init__(self, plan: Plan, book: Book, inventory: Dict[str, Fraction]):
        self.plan, self.book, self.opening_inventory = plan, book, inventory

    def unclosed(self) -> Dict[Tuple[str, int], Fraction]:
        """Process accounts not back at zero at a month end (a process that does not conserve)."""
        out = {}
        for key, p in self.plan.procs.items():
            for a in sorted(n for n in self.book.accounts if n.endswith("]") and n[:n.rindex("[")] == key):
                for m in range(MONTHS):
                    v = self.book.balance(a, m)
                    if v != 0:
                        out[(a, m)] = v
        return out

    def stranded(self) -> Dict[str, Fraction]:
        """Internal streams whose stock grows over the year: matter sent on and never taken."""
        out = {}
        for a in self.plan.streams:
            if a not in self.book.accounts:                          # never posted (a zero stream)
                continue
            growth = self.book.balance(a, MONTHS - 1) - self.book.opening(a, 0)
            if growth != 0:
                out[a] = growth
        return out


def load(case_path) -> Network:
    """Read a case file's ``network`` block; ``registry`` and ``params`` are paths beside it.

    Every key of the block must be read (:meth:`~one_engine.inputs.Reader.finish`).
    """
    path = pathlib.Path(case_path)
    top = Reader(load_yaml_strict(path.read_text()), source=path.name)
    r = Reader(top.take("network"), source=f"{path.name}:network")
    registry = load_yaml_strict((path.parent / r.take("registry")).read_text())
    params = load_yaml_strict((path.parent / r.take("params")).read_text())
    net = Network(registry, params, r)
    r.finish()
    top.finish()
    return net
