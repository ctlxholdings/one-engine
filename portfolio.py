"""Portfolio and grain: copies of a template on a deployment schedule (module card 12).

What this module is
-------------------
A programme of many identical assets (1,260 rooftop SPVs, 50 hectares of a
plantation, a pool of 1,400 buildings) is modelled as one *template*
assembly and a *deployment schedule*: how many copies start in each period.
This module answers how many copies exist, when, and what their sum looks
like at the grain the case asks for. It never creates legal entities (that
belongs to :mod:`one_engine.entities`): a cohort of 90 SPVs is a scaled
series here, not 90 books.

The grain built so far is the representative line, the one Averta's live
model uses (``engine_v2.runners.run_averta.aggregate_portfolio``): the
template is run once, and the portfolio's series is the template's series
shifted to each cohort's start and multiplied by the cohort's count::

    out[t] = sum over cohorts c of  count[c] x template[t - c + align]
             for first <= t - c + align < len(template)

``align`` is the template period that falls on a cohort's own deployment
period (Averta's reports put the template's COD, period 1, there), and
``first`` is the earliest template period read (Averta reads from COD on,
so the template's construction period, where its equity is contributed,
never enters).

Declared assumption: shifting is exact only while the template does not
depend on the calendar date (its escalation counts from its own COD, not
from a calendar year). Averta's reference SPV is written on periods only,
so it holds there; a template with dated terms needs one run per cohort,
which is not built.

Invariant owned
---------------
The aggregate is linear in the schedule: a programme cut from 50 units to 30
changes every aggregated series by 30/50 exactly, and nothing else
(``tests/test_portfolio.py``). All arithmetic is exact.

The ``cohorts`` generator
-------------------------
The case runner (:mod:`one_engine.cases`) offers :func:`cohorts` as the
generator kind ``cohorts``. It runs the template case, reads the series the
case file names from the template's cuts, and returns them aggregated::

    portfolio:
      kind: cohorts
      template: averta_spv.yaml          # a case file in one_engine/cases
      set: {generators.revenue.specific_yield: 1443.0}   # optional overrides
      schedule: [30, 30, 60, 60, 90, ...]                 # copies started per period
      periods: 20                                         # length of the result
      align: 1
      first: 1
      series:
        distributions: {cut: equity_flows, times: -1}
        revenue: {cut: revenue, running: true}
      generated: {zones: sumproduct.zones}               # optional

``times`` multiplies the cut (the template's cash cut of its equity is
negative for a distribution); ``running`` reads its running total, which is
what a profit-and-loss account's closing balance is when the books are
never closed to retained earnings (the live model's portfolio revenue and
opex arrays are such closings). ``generated`` passes on outputs of the
template's own generators (``<generator>.<output>``), so a case can value
the portfolio on the template's zones without restating them. Outputs: each
named series and passed-on output, ``online`` (the copies started so far,
by period), ``deployed`` (the schedule over the result's periods) and
``count`` (all copies in the schedule).
"""
from __future__ import annotations

import itertools
from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping, Sequence

from one_engine.inputs import InputError, Reader
from one_engine.units import exact


def deploy(template: Sequence[Fraction], schedule: Sequence[int], periods: int,
           align: int = 0, first: int = 0) -> List[Fraction]:
    """The sum of ``schedule[c]`` copies of ``template`` started at each period ``c``.

    Parameters
    ----------
    template:
        The template's series, one value per template period.
    schedule:
        Copies started in each period (non-negative whole numbers).
    periods:
        Length of the result.
    align:
        The template period that falls on a cohort's deployment period.
    first:
        The earliest template period read; earlier ones never enter.

    Raises
    ------
    InputError
        If a count is negative or not a whole number.
    """
    for c, n in enumerate(schedule):
        if n < 0 or n != int(n):
            raise InputError(f"deploy: period {c} starts {n} copies; a count is a non-negative whole number")
    out = [Fraction(0)] * periods
    for c, n in enumerate(schedule):
        if not n:
            continue
        for t in range(periods):
            k = t - c + align
            if first <= k < len(template):
                out[t] += int(n) * template[k]
    return out


def online(schedule: Sequence[int], periods: int) -> List[int]:
    """Copies started so far, by period (a copy counts from its own start period)."""
    padded = list(schedule[:periods]) + [0] * max(0, periods - len(schedule))
    return [int(x) for x in itertools.accumulate(padded)]


def cohorts(r: Reader, run: Callable[[str, Mapping[str, Any]], Any]) -> Dict[str, Any]:
    """The ``cohorts`` generator: run the template, aggregate the named cuts over the schedule.

    ``run(template, overrides)`` is supplied by the case runner (it resolves
    the template's file and applies the dotted-path overrides), so this
    module does not import the case runner.
    """
    template = r.take("template")
    overrides = dict(r.take_or_default("set", {}, reason="the template is run as written").value)
    schedule = [exact(x) for x in r.take("schedule")]
    periods = int(r.take("periods"))
    align = int(r.take("align"))
    first = int(r.take("first"))
    specs = dict(r.take("series"))
    passed = dict(r.take_or_default("generated", {}, reason="no template generator output is passed on").value)
    if not specs:
        raise InputError("cohorts: no series is named")
    result = run(template, overrides)
    out: Dict[str, Any] = {}
    for name, raw in specs.items():
        s = Reader(raw, source=f"cohorts.series.{name}")
        cut = s.take("cut")
        times = exact(s.take_or_default("times", 1, reason="the cut is read as it is").value)
        running = bool(s.take_or_default("running", False, reason="the cut's own values").value)
        s.finish()
        if cut not in result.cuts:
            raise InputError(f"cohorts: series '{name}' reads cut '{cut}', which template {template} does not make")
        values = [times * v for v in result.cuts[cut].values]
        if running:
            values = list(itertools.accumulate(values))
        out[name] = deploy(values, schedule, periods, align, first)
    for name, ref in passed.items():
        g, _, o = str(ref).partition(".")
        if g not in result.generated or o not in result.generated[g]:
            raise InputError(f"cohorts: '{name}' passes on '{ref}', which template {template} does not generate")
        if name in out:
            raise InputError(f"cohorts: '{name}' is both a series and a passed-on output")
        out[name] = result.generated[g][o]
    if any(k in out for k in ("online", "deployed", "count")):
        raise InputError("cohorts: online, deployed and count are the generator's own outputs")
    out["online"] = online(schedule, periods)
    out["deployed"] = (list(schedule[:periods]) + [Fraction(0)] * periods)[:periods]
    out["count"] = sum(schedule, Fraction(0))
    return out
