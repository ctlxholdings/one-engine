"""Credit-framework pilot: who bears each risk, read from runs of two real deals (Ali, 2026-10-15).

The mapping (``lenders_view/meta/MAPPING_credit_to_one_engine.md``) put the
credit framework on the engine with two core changes, each needed by both
pilot deals:

* **C1**, classes on inputs: :class:`one_engine.inputs.Scheme` classifies
  inputs by name, so the credit scheme says which risk kind each input a
  world moves belongs to;
* **C2**, combined results per party: :func:`one_engine.runs.exposure`,
  each party's cut in a world less the same cut in the run it is read
  against.

Everything else is pack and case data: :mod:`one_engine.packs.credit` reads a
lens (``cases/credit/*.yaml``: the deal, its parties, its credit scheme, its
worlds and runs) and derives the bearers.

The two deals:

* **fi-compass** (``cases/ficompass_deal.yaml``): the source's three
  performance cases, booked by the oracle translation as parallel entities,
  restated as one deal and worlds. The worlds reproduce the translation's
  parallel cases to the cent, so the restated deal is the oracle's deal; the
  ESIF-default world is new, a credit world the source does not run.
* **Averta's buyer** (``cases/averta_buyer.yaml``): the live model's P90 as
  a world on its P50 base.

What the pins say (the readings, with their comparison to the hand-drawn
credit graphs and esco-product's rows, are in the LOG and the pilot report):

* fi-compass: a 10% savings shortfall costs the provider 24,000 a year and
  the municipality 500 (its guaranteed saving, 245,000, exceeds the fee,
  240,000, so it loses the gap's share); the forfeiter loses nothing. At 75%
  ESIF pays 20,000 a year, and the forfeiter still loses nothing. If ESIF
  then does not pay, the forfeiter loses exactly that: the forfeiter's
  exposure to savings risk is ESIF's credit.
* Averta's buyer: the P90 yield leaves the senior lender's receipts
  unchanged in every quarter; the buyer's equity bears all of it.
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import CASES, run_case
from one_engine.inputs import InputError, load_yaml_strict
from one_engine.oracles.esco import _case, esco_inputs
from one_engine.packs.credit import read_lens

LENSES = CASES / "credit"
REPORT = json.loads((CASES / "reference" / "averta_live" / "report_data.json").read_text())


@pytest.fixture(scope="module")
def fi():
    return read_lens(LENSES / "ficompass.yaml")


@pytest.fixture(scope="module")
def averta():
    return read_lens(LENSES / "averta_buyer.yaml")


def totals(reading):
    return {p: sum(xs) for p, xs in reading.exposure.items()}


# ── the restated fi-compass deal is the oracle's deal ────────────────────────

ORACLE = esco_inputs(_case("ficompass_example"))


def test_the_deals_inputs_are_the_oracles():
    deal = load_yaml_strict((CASES / "ficompass_deal.yaml").read_text())
    given = deal["generators"]["src"]["inputs"]
    switches = {"achievement_pct", "esif_pays"}
    for name, v in given.items():
        if name not in switches:
            assert F(str(v)) == ORACLE[name], name
    assert given["achievement_pct"] == ORACLE["s100__achievement_pct"] and given["esif_pays"] == 1


def test_the_lens_worlds_are_the_sources_cases():
    lens = load_yaml_strict((LENSES / "ficompass.yaml").read_text())
    for case in ("s90", "s75"):
        assert lens["worlds"][case]["set"]["generators.src.inputs.achievement_pct"] == ORACLE[f"{case}__achievement_pct"]


def _translation():
    """The oracle translation's own run, fed the oracle's inputs as the esco harness feeds them."""
    tr = load_yaml_strict((CASES / "esco" / "ficompass_example.yaml").read_text())
    case = tr["case"]
    case["generators"]["src"]["inputs"] = {k: ([str(x) for x in v] if isinstance(v, list) else str(v))
                                           for k, v in ORACLE.items()}
    return run_case(case)


def test_the_worlds_reproduce_the_translations_parallel_cases(fi):
    tr = _translation()
    s90, s75 = fi.runs["s90"], fi.runs["s75"]
    # the municipality's net (savings less fees) and the provider's fees, period by period
    assert s90.cut("party_municipality").values == tr.cut("net_90").values
    assert s75.cut("party_municipality").values == tr.cut("net_75").values
    assert s90.cut("party_provider").values[1:] == tr.cut("provider_90").values[1:]
    # what ESIF pays out at 75%
    assert s75.cut("party_esif").values == tr.cut("esif_cover").values


# ── fi-compass: who bears what ──────────────────────────────────────────────

def test_a_savings_shortfall_lands_on_the_provider_not_the_forfeiter(fi):
    r = fi.readings["s90"]
    assert r.risks == {"achievement_pct": "operating_performance"}
    assert totals(r) == {"municipality": -5000, "provider": -240000, "forfeiter": 0, "esif": 0}
    assert r.bearers == ["municipality", "provider"]


def test_at_75_percent_esif_pays_the_forfeiter_the_shortfall(fi):
    r = fi.readings["s75"]
    assert totals(r) == {"municipality": -12500, "provider": -400000, "forfeiter": 0, "esif": -200000}
    assert r.exposure["esif"][1:] == [-20000] * 10
    assert r.bearers == ["esif", "municipality", "provider"]


def test_if_esif_does_not_pay_the_forfeiter_bears_exactly_what_esif_covered(fi):
    r = fi.readings["s75_esif_default"]
    assert r.against == "s75"
    assert r.risks == {"esif_pays": "counterparty_default"}
    assert totals(r) == {"municipality": 0, "provider": 0, "forfeiter": -200000, "esif": 200000}
    assert r.bearers == ["forfeiter"]


def test_the_books_stay_whole_in_every_world(fi):
    for name, res in fi.runs.items():
        assert res.failed_checks() == [], name


# ── Averta's buyer at P90 ───────────────────────────────────────────────────

def test_the_p90_world_is_the_live_models_p90(averta):
    lens = load_yaml_strict((LENSES / "averta_buyer.yaml").read_text())
    add = lens["worlds"]["p90"]["add"]
    y = REPORT["metadata"]["p90_specific_yield"]
    assert add["generators.portfolio"]["set"]["generators.revenue.specific_yield"] == F(str(y))
    assert add["generators.holdco"]["set"]["generators.portfolio.set"]["generators.revenue.specific_yield"] == F(str(y))


def test_at_p90_the_senior_lender_is_paid_in_full_and_the_equity_bears_the_yield(averta):
    r = averta.readings["p90"]
    assert r.risks == {"specific_yield": "revenue_quantity"}
    assert all(x == 0 for x in r.exposure["senior_lender"])
    assert r.bearers == ["buyer_equity"]
    # the live report's P90 FCFE is 8.46M below its P50; here the equity's loss is pinned to the cent
    assert round(float(totals(r)["buyer_equity"]), 2) == pytest.approx(-8464661.41, abs=0.01)


# ── the lens refuses what it cannot read ────────────────────────────────────

def _lens(tmp_path, **edits):
    doc = load_yaml_strict((LENSES / "ficompass.yaml").read_text())
    doc.update(edits)
    import yaml
    p = tmp_path / "lens.yaml"
    p.write_text(yaml.safe_dump(json.loads(json.dumps(doc, default=str)), sort_keys=False))
    return p


def test_a_world_no_run_uses_refuses(tmp_path):
    with pytest.raises(InputError, match="worlds no run uses: esif_default"):
        read_lens(_lens(tmp_path, runs={"s90": {"worlds": ["s90"]}, "s75": {"worlds": ["s75"]}}))


def test_a_run_read_against_a_run_it_does_not_extend_refuses(tmp_path):
    runs = {"s90": {"worlds": ["s90"]}, "s75": {"worlds": ["s75"]},
            "esif": {"worlds": ["s90", "esif_default"], "against": "s75"}}
    with pytest.raises(InputError, match="esif.*does not layer on s75"):
        read_lens(_lens(tmp_path, runs=runs))


def test_a_world_moving_an_input_the_credit_scheme_does_not_classify_refuses(tmp_path):
    with pytest.raises(InputError, match="does not classify: esif_pays"):
        read_lens(_lens(tmp_path, risks={"achievement_pct": "operating_performance"}))
