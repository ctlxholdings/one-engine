"""ACTUS test vectors as an oracle family: 277 published contracts, 19 contract types.

The oracle
----------
``cases/reference/actus/actus-tests-<stem>.json``, vendored unmodified from
``actusfrf/actus-tests`` (CC BY-SA 4.0). Each test holds the contract terms,
the market data it observes, and the answer: the dated event stream with,
per event, its payoff and the state after it (notional, nominal rate,
accrued interest).

Translation
-----------
None. The terms and the observed data go to :func:`one_engine.packs.actus.run`
exactly as stored; the receipt lists them. That makes this family the
cleanest test of the engine: only the engine can be wrong.

Checks, per oracle
------------------
* ``events``: the list of (date, type) pairs is identical, in order;
* per event, ``payoff``, ``notionalPrincipal``, ``nominalInterestRate`` and
  ``accruedInterest`` agree within 1e-8 relative (floor 1): the corpus
  prints some values to ten digits;
* ``ledger balanced``: every period of the holder's book closes on the
  per-unit identity, and ``flows paired``: no flow lacks its counter-leg.
  A swap is held in two books, one per leg; both are checked.
  The standard does not cover the ledger; these are the engine's own laws,
  run on every oracle.

Planted error
-------------
``notionalPrincipal`` × 1.01; the nominal rate + 1% when a contract has no
notional or the engine refuses the planted notional. Either way any stated
purchase or termination price is also × 1.01: an FX outright terminated
before maturity pays only its prices. A comparison that still agrees after
that is blind.

Ids
---
``<stem>/<test id>``: the file stem and the test's key, because two files
reuse a key (``ad0`` holds a contract named ``ann01``, as does ``ann``).
"""
from __future__ import annotations

import copy
import json
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

from one_engine.dates import instant
from one_engine.oracles import REFERENCE, Blocked, Check, Family
from one_engine.packs import actus

FOLDER = "actus"
FIELDS = (("payoff", "payoff"), ("notional", "notionalPrincipal"), ("rate", "nominalInterestRate"),
          ("accrued", "accruedInterest"))
REL = 1e-8
#: Answer keys that are not values to compare: the row's identity and its currency label.
NOT_VALUES = {"eventDate", "eventType", "currency"}


@lru_cache(maxsize=None)
def _file(stem: str) -> Dict[str, Any]:
    return json.loads((REFERENCE / FOLDER / f"actus-tests-{stem}.json").read_text())


def _stems() -> List[str]:
    return sorted(p.stem[len("actus-tests-"):] for p in (REFERENCE / FOLDER).glob("actus-tests-*.json"))


def _close(got: Any, want: Any) -> bool:
    g, w = float(got), float(want)
    return abs(g - w) <= REL * max(1.0, abs(w))


#: Prices planted with every variant: an FX outright bought and terminated before maturity
#: (fxout06, fxout08, fxout10) pays its two prices and nothing that depends on its notional.
PLANTED_PRICES = ("priceAtPurchaseDate", "priceAtTerminationDate")


def _plants(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The planted variants, in order of preference: notional x 1.01, then rate + 0.01; each with
    any stated purchase or termination price x 1.01."""
    out = []
    for term, change in (("notionalPrincipal", lambda v: v * Fraction(101, 100)),
                         ("nominalInterestRate", lambda v: v + Fraction(1, 100))):
        if case["terms"].get(term) not in (None, ""):
            c = copy.deepcopy(case)
            c["terms"][term] = str(change(Fraction(str(c["terms"][term]).strip())))
            for price in PLANTED_PRICES:
                if c["terms"].get(price) not in (None, ""):
                    c["terms"][price] = str(Fraction(str(c["terms"][price]).strip()) * Fraction(101, 100))
            out.append(c)
    return out


def _run_planted(case: Dict[str, Any], asked) -> "actus.Run":
    """Run the first planted variant the engine accepts. A notional that no longer fits its
    redemption amounts (lax12: five redemptions of 20 do not clear 101) refuses, and then the
    rate is planted instead, so the check is still put to the test."""
    for c in _plants(case):
        try:
            return actus.run(c, analysis=asked)
        except actus.ActusError:
            continue
    raise Blocked("no planted variant runs, so the check cannot be shown to fail")


class ActusFamily(Family):
    name = "actus"
    planted = ("notionalPrincipal x 1.01 (nominal rate + 0.01 when there is no notional or it is refused), "
               "with any stated purchase or termination price x 1.01")
    tolerance = "event list identical; each value within 1e-8 relative (floor 1), as the corpus prints to ten digits"

    def oracles(self) -> List[str]:
        return [f"{s}/{k}" for s in _stems() for k in sorted(_file(s))]

    def source(self, oid: str) -> str:
        return f"{FOLDER}/actus-tests-{oid.split('/')[0]}.json"

    def case(self, oid: str) -> Dict[str, Any]:
        stem, key = oid.split("/", 1)
        return _file(stem)[key]

    def run(self, oid: str, plant: bool = False) -> Tuple[List[Check], Dict[str, Any]]:
        case = self.case(oid)
        kind = case["terms"].get("contractType")
        if kind not in actus.BUILT_TYPES:
            raise Blocked(f"ACTUS contract type {kind} is not built "
                          f"(packs/actus.py builds {', '.join(actus.BUILT_TYPES)})")
        inputs = {"terms": case["terms"], "dataObserved": sorted(case.get("dataObserved") or {}),
                  "eventsObserved": case.get("eventsObserved") or [], "to": case.get("to"),
                  "translation": "none: passed verbatim to packs.actus.run"}
        # An analysis date is a question ("report the state at this instant"), not an answer. ad0
        # states its one AD nowhere but on the answer row, so the instant is read from there and
        # said so; the values on that row are still compared like any other.
        stated = {instant(e["time"]) for e in inputs["eventsObserved"] if e.get("type") == "AD"}
        asked = tuple(w["eventDate"] for w in case["results"]
                      if w["eventType"] == "AD" and instant(w["eventDate"]) not in stated)
        if asked:
            inputs["analysis"] = {"instants": list(asked), "read from": "the answer's AD rows (dates only)"}
        try:
            r = _run_planted(case, asked) if plant else actus.run(case, analysis=asked)
        except actus.ActusError as e:
            raise Blocked(str(e).split(": ", 1)[-1]) from e
        got, want = r.stream(), case["results"]
        g_ev = [(x["date"], x["type"]) for x in got]
        w_ev = [(instant(w["eventDate"]), w["eventType"]) for w in want]
        checks = [Check("events", f"{len(w_ev)} events", str(len(w_ev)), str(len(g_ev)), g_ev == w_ev)]
        if g_ev != w_ev:
            i = next((i for i, (a, b) in enumerate(zip(g_ev, w_ev)) if a != b), min(len(g_ev), len(w_ev)))
            show = lambda ev: f"{ev[i][0].date()} {ev[i][1]}" if i < len(ev) else "none"
            checks.append(Check("event", f"#{i + 1}", show(w_ev), show(g_ev), False))
        known = {theirs for _, theirs in FIELDS}
        for g, w in zip(got, want):
            at = f"{w['eventDate'][:10]} {w['eventType']}"
            for ours, theirs in FIELDS:
                if theirs in w:
                    checks.append(Check(theirs, at, str(w[theirs]), repr(float(g[ours])), _close(g[ours], w[theirs])))
            # a value the answer states and the engine does not report is a failed check, not a skip
            for extra in sorted(set(w) - known - NOT_VALUES):
                checks.append(Check(extra, at, str(w[extra]), "not reported", False))
        # a swap is held in two books, one per leg (packs.actus.SwapRun)
        bad = [t for leg in r.legs() for t in range(len(leg.plan.instants))
               if any(v != 0 for v in leg.group.book("holder").imbalance(t).values())]
        checks.append(Check("ledger balanced", "every period", "0 unbalanced periods", f"{len(bad)}", not bad))
        unpaired = [u for leg in r.legs() for u in leg.group.unpaired_flows()]
        checks.append(Check("flows paired", "every flow", "0 unpaired", str(len(unpaired)), not unpaired))
        return checks, inputs

    def inventory_oid(self, row: Mapping[str, str]) -> Optional[str]:
        """Claim the golds copy (one row per test) of the corpus; it is the origin of these files."""
        if row.get("encoding_id") != "g_actus_vector":
            return None
        stem = row["path"].rsplit("actus-tests-", 1)[-1].removesuffix(".json")
        return f"{stem}/{row['case_id']}"
