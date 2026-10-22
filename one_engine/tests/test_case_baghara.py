"""Baghara's mass balance on the matter ledger (plan step 7d).

Referees:

1. **Parity with the old engine** (``cases/reference/baghara_old_engine.json``,
   written by ``consolidation/experiments/make_reference_baghara.py`` from
   baghara 59bcdcf at 1, 10 and 50 farm units). Every stream of all 32
   processes matches to 1e-12 relative, except where the old engine rounds
   a livestock hand-off to two decimals before using it downstream. Fed the
   old engine's rounded hand-offs, the new engine matches every stream, so
   that rounding is the whole divergence.
2. **The kernel's laws on the network**: each unit balances against its
   boundaries every month, no stock goes below zero, every process is back
   at zero at each month end, and in steady state every stream ends the
   year where it began, except matter the registry sends on and nobody
   takes.
3. **Scale invariance** (Baghara's own check): 50 units post exactly five
   times what 10 units post, every amount.

The registry and the dossier are Baghara's files, copied unchanged.
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.inputs import Reader, State, load_yaml_strict
from one_engine.kernel import KernelError
from one_engine.packs.process import MONTHS, Network, ProcessError, load

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
REG = load_yaml_strict((HERE / "baghara" / "uof_process_registry.yaml").read_text())
PAR = load_yaml_strict((HERE / "baghara" / "uof_promoter_v2.yaml").read_text())
CASE = load_yaml_strict((HERE / "baghara.yaml").read_text())["network"]
OLD = json.loads((HERE / "reference" / "baghara_old_engine.json").read_text())["runs"]
HANDOFF = {"A2_beef", "B2_dairy"}
DOWNSTREAM_OF_HANDOFF = {"A3_abattoir", "B3_dairy_plant", "ST_dry", "ST_cold", "TR_dry", "TR_cold", "MKT_products"}


def build(reg=REG, par=PAR, case=CASE) -> Network:
    r = Reader({k: v for k, v in case.items() if k not in ("registry", "params")}, "baghara.yaml:network")
    return Network(reg, par, r)


@pytest.fixture(scope="module")
def run50():
    return load(HERE / "baghara.yaml").plan(50).post()


def old_items(d):
    items = [(s["name"], s["mass_t"]) for s in d["streams"]]
    if d["handoff"]:
        items += [("product." + i["name"], i["mass_t"]) for i in d["handoff"]["inputs"] + d["handoff"]["outputs"]]
    return items


def mismatches(plan, n):
    out = []
    for proc, d in OLD[str(n)].items():
        for name, m in old_items(d):
            v = float(plan.value(proc, name))
            if abs(v - m) > 1e-12 * max(abs(m), 1.0):
                out.append((proc, name))
    return out


# ── parity ────────────────────────────────────────────────────────────────

def test_the_case_file_has_no_unread_key(tmp_path):
    case = dict(CASE, registry=str(HERE / "baghara" / "uof_process_registry.yaml"),
                params=str(HERE / "baghara" / "uof_promoter_v2.yaml"), typo_key=1)
    path = tmp_path / "case.yaml"
    path.write_text(json.dumps({"network": case}))           # JSON is YAML
    with pytest.raises(Exception, match="read by nothing: typo_key"):
        load(path)


@pytest.mark.parametrize("n", [1, 10, 50])
def test_every_stream_matches_the_old_engine_except_the_rounded_handoffs(n):
    plan = build().plan(n)
    bad = mismatches(plan, n)
    assert bad, "the old engine's rounding should show"
    assert {p for p, _ in bad} <= HANDOFF | DOWNSTREAM_OF_HANDOFF
    assert len(OLD[str(n)]) == 32


@pytest.mark.parametrize("n", [1, 10, 50])
def test_with_the_old_engines_rounded_handoffs_every_stream_matches(n):
    reg = copy.deepcopy(REG)
    for proc in HANDOFF:
        old = {i["name"]: i["mass_t"] for i in OLD[str(n)][proc]["handoff"]["inputs"] + OLD[str(n)][proc]["handoff"]["outputs"]}
        for side in ("inputs", "outputs"):
            for item in reg[proc]["product_handoff"].get(side) or []:
                item["mass"] = repr(old[item["name"]])
    assert mismatches(build(reg).plan(n), n) == []


def test_the_rounding_the_old_engine_applies():
    plan = build().plan(50)
    assert plan.value("A2_beef", "product.finished_cattle") == F("11.6982")     # old: 11.70
    assert plan.value("B2_dairy", "product.raw_milk") == F("66.744")            # old: 66.74
    assert plan.value("B2_dairy", "product.calves_weaned") == F("1.836")        # old: 1.84
    assert plan.value("A3_abattoir", "finished_cattle") == F("584.91")          # old: 585.00
    assert plan.value("B3_dairy_plant", "raw_milk") == F("3337.2")              # old: 3337.00


# ── the kernel's laws on the network ─────────────────────────────────────

def test_each_unit_balances_every_month(run50):
    for m in range(MONTHS):
        assert run50.book.imbalance(m) == {"t": 0, "t_DM": 0}


def test_every_process_is_back_at_zero_at_each_month_end(run50):
    assert run50.unclosed() == {}


def test_the_dairy_milk_pathway_is_sent_to_the_plant_and_never_taken(run50):
    # B2 sends 25% of feed dry matter to "B3"; the plant draws raw milk from
    # the wet hand-off instead, so 35.475 t DM per farm piles up every year.
    assert run50.stranded() == {"B2_dairy.milk_pathway_dm": F("35.475") * 50}


def test_the_herds_outputs_have_no_physical_source_and_are_named(run50):
    plan = run50.plan
    assert plan.value("A2_beef", "gap") == F("11.6982") - 9                      # live-weight gain, t per farm
    assert plan.value("B2_dairy", "gap") == F("66.744") + F("1.836") + F("2.4")  # milk, calves, culls, t per farm
    b = run50.book
    for g in ("liveweight_gain_unsourced[t]", "herd_output_unsourced[t]"):
        assert State.STAND_IN in set().union(*(b.states(g, m) for m in range(MONTHS)))


def test_without_a_named_gap_the_herd_overdraws_and_the_kernel_refuses():
    case = dict(CASE, gaps={})
    with pytest.raises(KernelError, match=r"floor: 'A2_beef\[t\]'"):
        build(case=case).plan(50).post()


def test_the_feed_deficit_is_bought_where_the_registry_says_and_flagged(run50):
    plan = run50.plan
    assert plan.value("A1_B1_feed", "other_feed_sources") == F("1389.09")       # old engine: node "gap.TBD"
    supply = run50.book.states("X1_Supply[t_DM]", 0)
    assert State.STAND_IN in supply


def test_wet_byproducts_enter_the_feed_balance_through_a_declared_conversion(run50):
    b = run50.book
    wet = sum(b.balance("convert[t]", m) - b.opening("convert[t]", m) for m in range(MONTHS))
    dry = sum(b.balance("convert[t_DM]", m) - b.opening("convert[t_DM]", m) for m in range(MONTHS))
    assert -wet == F("961.95") + F("727.5")             # oilcake and bran leave the wet balance
    assert dry == F("865.755") + F("640.2")             # and enter the dry one at 90% and 88%


def test_a_cross_unit_draw_without_a_conversion_refuses():
    with pytest.raises(ProcessError, match="no declared conversion"):
        build(case=dict(CASE, conversions={})).plan(50)


def test_a_conversion_factor_that_does_not_multiply_the_term_refuses():
    conv = dict(CASE["conversions"], **{"A1_B1_feed.hub_bran": "forage_yield_dm"})
    with pytest.raises(ProcessError, match="does not multiply"):
        build(case=dict(CASE, conversions=conv)).plan(50)


def test_carried_stocks_show_what_the_monthly_weights_require(run50):
    inv = run50.opening_inventory
    # Mango is harvested Jul-Sep but dried from April: 900 t of fresh mango,
    # three quarters of the year's 1,200 t, must be held from last season.
    assert inv["G1_mango.net_product"] == 900
    # Onion is lifted Nov-Dec and graded mostly Jan-Mar.
    assert inv["F1_onion.net_product"] == 10125
    assert run50.book.balance("G1_mango.net_product", MONTHS - 1) == 900       # the year closes where it began


def test_a_hub_that_forgets_the_unit_count_strands_the_farms_output():
    # Per-process closure (the old engine's check) still passes; the network does not.
    reg = copy.deepcopy(REG)
    reg["C2_oil_press"]["driver"] = "C1_peanut.net_grain + C1_sesame.net_grain * uof_count"
    run = build(reg).plan(50).post()
    assert run.unclosed() == {}
    assert run.stranded()["C1_peanut.net_grain"] == F("29.1") * 49


def test_a_process_whose_ratios_do_not_sum_to_one_does_not_close():
    reg = copy.deepcopy(REG)
    reg["ST_dry"]["outputs"][1]["ratio"] = "0"                 # shrinkage dropped
    run = build(reg).plan(10).post()
    assert ("ST_dry[t]", 2) in run.unclosed()


# ── scale invariance ──────────────────────────────────────────────────────

def test_fifty_units_post_exactly_five_times_ten():
    a, b = build().plan(10), build().plan(50)
    ma, mb = a.monthly(), b.monthly()
    for m in range(MONTHS):
        assert [(l.debit, l.credit, 5 * x) for l, x in ma[m]] == [(l.debit, l.credit, x) for l, x in mb[m]]
    assert {k: 5 * v for k, v in a.opening_inventory().items()} == b.opening_inventory()


# ── refusals ──────────────────────────────────────────────────────────────

def test_an_unresolved_parameter_path_refuses():
    reg = copy.deepcopy(REG)
    reg["C1_peanut"]["params"]["loss_pct"] = "oilseeds.crops.peanut.loss_pct_typo"
    with pytest.raises(ProcessError, match="does not resolve"):
        build(reg).plan(1)


def test_a_parameter_with_neither_base_nor_value_refuses():
    par = copy.deepcopy(PAR)
    node = par["oilseeds"]["crops"]["peanut"]["loss_pct"]
    par["oilseeds"]["crops"]["peanut"]["loss_pct"] = {k: v for k, v in node.items() if k not in ("base", "value")}
    with pytest.raises(ProcessError, match="neither 'base' nor 'value'"):
        build(par=par).plan(1)


def test_a_registry_key_the_pack_does_not_read_refuses():
    reg = copy.deepcopy(REG)
    reg["C1_peanut"]["moisture_pct"] = "0.1"
    with pytest.raises(ProcessError, match="not read by this pack"):
        build(reg)


def test_a_subtracted_stream_refuses():
    reg = copy.deepcopy(REG)
    reg["ST_dry"]["driver"] = "D2_mill.flour - C2_oil_press.crude_oil"
    with pytest.raises(ProcessError, match="subtracted"):
        build(reg).plan(1)
