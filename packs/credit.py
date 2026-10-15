"""Credit pack: who bears each risk, derived from runs of the deal (credit pilot, 2026-10-15).

What this pack is for
---------------------
Ali's lender credit framework (``lenders_view/credit_framework.yaml``,
``meta/credit_meta.yaml``) records, for each risk of a deal, who bears it.
On paper that is declared. On a modelled deal it can be read: run a world
that moves the risk's input and see whose position falls. The mapping
(``lenders_view/meta/MAPPING_credit_to_one_engine.md``) puts this on the
engine with two small core pieces, and this pack is the rest:

* **C1** :class:`one_engine.inputs.Scheme` classifies inputs by name. A
  lens's ``risks`` block is the credit scheme: input name -> risk kind (the
  kinds of ``credit_meta.yaml``). A world moving an input the scheme does
  not classify refuses, so no risk drops out of the reading.
* **C2** :func:`one_engine.runs.exposure`: each party's cut in a run less
  the same cut in the run it is read against.

A bearer found this way is DERIVED (the engine's :class:`~one_engine.inputs.State`
names). Where a mechanism is not modelled, the bearer stays STATED, read from
a source; that half of the framework is not in this pack yet.

The lens file
-------------
``cases/credit/<name>.yaml``::

    lens: name
    base: a case file in cases/ (the deal, as it runs on its own)
    parties: {party: {cut: <a cut spec, as in a case's cuts>, sign: 1 | -1}}
    risks:   {input name: risk kind}                 the credit scheme (C1)
    worlds:  {name: <a world overlay, one_engine.runs format, without 'world'>}
    runs:    {name: {worlds: [world, ...], against: base | an earlier run}}

Each party's cut is added to the deal as ``party_<party>`` (a name the deal
already uses refuses). ``sign`` is 1 when the cut is the party's own
position, -1 when it is read from the other side (the lender's receipts
are the borrower's payments). A run layers its worlds on the base in order;
it is read against the base, or against an earlier run whose worlds it
starts with (the ESIF default is read against the 75% case it breaks
further), and its risks are the inputs moved by the worlds it adds. Every
world must be used by some run.

A party bears a run's risk when its position falls over the run (the sum of
its exposure is below zero). The sum is undiscounted: the pilot asks who
bears, not how much it is worth.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, List, Mapping

from one_engine.inputs import InputError, Reader, Scheme, load_yaml_strict
from one_engine.runs import compose, exposure, moved


@dataclass(frozen=True)
class Reading:
    """One run read against another: the risks it moves and what each party gains or loses, by period."""

    run: str
    against: str
    risks: Dict[str, str]
    exposure: Dict[str, List[Fraction]]

    @property
    def bearers(self) -> List[str]:
        """The parties whose position falls over the run, sorted (DERIVED bearers)."""
        return sorted(p for p, xs in self.exposure.items() if sum(xs) < 0)


@dataclass
class Lens:
    """A lens read: every run's case result (``base`` included) and every run's reading."""

    name: str
    runs: Dict[str, Any]
    readings: Dict[str, Reading]


def read_lens(path: pathlib.Path) -> Lens:
    """Run the lens at ``path`` and read every run against its reference; see the module docstring.

    Raises
    ------
    InputError
        For a key nobody reads, a party cut name the deal already uses, a
        world no run uses, an unknown world or reference run, a run that does
        not layer on the run it is read against, or an input the credit
        scheme does not classify.
    """
    from one_engine.cases import CASES, run_case      # the engine, imported only when a lens runs

    path = pathlib.Path(path)
    r = Reader(load_yaml_strict(path.read_text()), source=path.name)
    name = r.take("lens")
    base = load_yaml_strict((CASES / r.take("base")).read_text())
    parties = r.take("parties")
    scheme = Scheme("credit", r.take("risks"))
    worlds = {w: {"world": w, **spec} for w, spec in r.take("worlds").items()}
    runs = r.take("runs")
    r.finish()

    cuts = base.setdefault("cuts", {})
    signs = {}
    for party, spec in parties.items():
        p = Reader(spec, source=f"{path.name}:parties.{party}")
        cut = f"party_{party}"
        if cut in cuts:
            raise InputError(f"{path.name}: the deal already has a cut named {cut}")
        cuts[cut] = p.take("cut")
        signs[party] = (cut, int(p.take("sign")))
        p.finish()

    idle = sorted(set(worlds) - {w for spec in runs.values() for w in spec["worlds"]})
    if idle:
        raise InputError(f"{path.name}: worlds no run uses: {', '.join(idle)}")

    results: Dict[str, Any] = {"base": run_case(base)}
    layers: Dict[str, List[str]] = {"base": []}
    readings: Dict[str, Reading] = {}
    for run, spec in runs.items():
        s = Reader(spec, source=f"{path.name}:runs.{run}")
        names = list(s.take("worlds"))
        against = s.take_or_default("against", "base", reason="a run is read against the base unless named").value
        s.finish()
        unknown = sorted(set(names) - set(worlds))
        if unknown:
            raise InputError(f"{path.name}: run {run} names worlds the lens does not have: {', '.join(unknown)}")
        if against not in layers:
            raise InputError(f"{path.name}: run {run} is read against {against}, which is not the base or an earlier run")
        if names[:len(layers[against])] != layers[against]:
            raise InputError(f"{path.name}: run {run} does not layer on {against} (its worlds do not start with {against}'s)")
        added = names[len(layers[against]):]
        risks = scheme.classify(sorted({i for w in added for i in moved(worlds[w])}))
        results[run] = run_case(compose(base, *(worlds[w] for w in names)))
        layers[run] = names
        readings[run] = Reading(run, against, risks, exposure(results[against], results[run], signs))
    return Lens(name, results, readings)
