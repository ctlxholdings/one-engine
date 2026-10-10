"""Macabacus short-form LBO, financing case 6 "LBO C", rebuilt from its inputs (plan step 7f).

Referee: the workbook itself (``cases/reference/macabacus_lbo.json``,
cached values of macabacus-lbo-short.xlsx, written by
``consolidation/experiments/make_reference_macabacus.py``). One_engine
reads only its inputs (the pre-deal balance sheet, the offer, the fair
value marks, the financing terms, the first forecast year and the last
four quarters) and must reproduce the close, every balance sheet and
income statement row for ten years, the cash flow statement, the deal
figures and the four holders' returns at the year-4 exit.

Tolerances: the workbook computes in binary floating point; one_engine in
exact fractions. Rows agree to 1e-9 in $ millions (largest difference seen
5.7e-13); the opening balance to 1e-13 (the copied floats are out by
4e-14); IRRs to 1e-8, the precision Excel's XIRR iterates to (largest
difference seen 5.8e-9).

What the case adds to the engine (convergence count 5): opening balance
sheets (``entities.open`` and the case's ``opening`` section, with one
balancing account), statement cuts (``cuts.pnl_cut`` and
``cuts.balance_cut``), holders (``holders.py``, new: dilution and holder
streams), ``measures.moic``, and the case runner's lenses, shared
``deals`` context and registries merged across packs. Pack changes (not
core): the corporate pack (financing fees, preferred stock, the
``acquisition`` and ``operating_plan`` rulebooks, the ``exit_returns``
lens) and, in the pf pack, a waterfall that retains its residual and an
income tax that starts late and tracks a nil tax base.

The findings tests pin what the workbook does that a reader might not
expect (they are its behaviour, reproduced, not endorsed).
"""
import copy
import itertools
import json
import pathlib
from decimal import Decimal
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.cuts import Scheme, balance_cut, pnl_cut
from one_engine.inputs import InputError, load_yaml_strict
from one_engine.kernel import KernelError

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "macabacus_lbo.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "macabacus_lbo.json").read_text())
I, O = REF["inputs"], REF["oracle"]
N = 11
EXIT = I["exit_year"]


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def book(res):
    return res.group.book("company")


def _scheme(name):
    return Scheme(name, CASE["schemes"][name])


def _d(x):
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(x)


def norm(x):
    """A value as Decimals, whether the YAML reader (Decimal) or the JSON reference (float) gave it."""
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [norm(v) for v in x]
    if isinstance(x, (int, float, Decimal)) and not isinstance(x, bool):
        return _d(x)
    return x


def mv(book, account, t):
    """An account's movement in period t, in its natural sign (an expense charged is positive)."""
    return book.balance(account, t) - book.opening(account, t)


def variant(edit):
    """Run a copy of the case with one edit applied."""
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


# ── the copy of the workbook's inputs ─────────────────────────────────────

def test_the_case_file_copies_the_workbooks_inputs():
    CN, IN = norm(CASE), norm(I)
    c, rb = CN["contracts"], CN["rulebooks"]
    stated = {k: v["value"] for k, v in CN["opening"]["company"].items()}
    assert stated.pop("retained_earnings") == "balancing"
    assert stated == {k: v for k, v in IN["pre_deal"].items()
                      if v and not k.startswith("total") and k != "retained_earnings"}
    for name, inst in IN["instruments"].items():
        if name == "preferred_a":
            assert c[name]["terms"]["rate"] == inst["fixed"]
            continue
        rate = c[name]["terms"]["rate"]
        assert rate[0] == 0
        want = [_d(inst["fixed"]) if inst["fixed"] else _d(lb) + _d(inst["spread"]) for lb in IN["libor"]]
        assert [_d(x) for x in rate[1:]] == want
        assert c[f"{name}_fee"]["terms"]["rate"] == inst["fee_rate"]
        assert c[f"{name}_fee"]["terms"]["periods"] == inst["fee_years"]
    assert c["revolver"]["terms"]["commitment"] == IN["revolver_commitment"]
    assert c["revolver"]["terms"]["commitment_fee_rate"][1:] == IN["commitment_fee_rate"]
    assert c["term_loan_a"]["terms"]["repay_periods"] == 1 / IN["instruments"]["term_loan_a"]["amortisation"]
    assert c["senior_note"]["terms"]["repay_from"] == IN["instruments"]["senior_note"]["bullet_year"]
    assert c["subordinated_note"]["terms"]["repay_from"] == IN["instruments"]["subordinated_note"]["bullet_year"]
    deal = rb["deal"]
    assert deal["offer"]["share_price"] == IN["share_price"] and deal["offer"]["premium"] == IN["offer_premium"]
    assert deal["offer"]["basic_shares"] == IN["basic_shares"] and deal["offer"]["options"] == IN["options"]
    assert deal["tax_rate"] == IN["tax_rate"] and deal["min_cash"] == IN["min_cash"]
    assert deal["marks"] == [{"account": "ppe_gross", "amount": IN["fv"]["ppe_mark"]},
                             {"account": "intangibles", "share_of_excess": IN["fv"]["intangibles_share"]}]
    items = deal["transaction_costs"]["items"]
    assert {k: items[k]["rate"] for k in ("advisory", "sponsor")} == IN["fee_rates"]
    assert {k: items[k] for k in ("legal", "accounting", "printing", "other")} == \
        {k: IN["uses"][f"{k}_fee"] for k in ("legal", "accounting", "printing", "other")}
    assert [s.get("amount") for s in deal["sources"][3:]] == [IN["sources"]["preferred_a"], IN["sources"]["management_equity"]]
    assert deal["sources"][3]["issue_cost"]["rate"] == IN["instruments"]["preferred_a"]["fee_rate"]
    ops = rb["operations"]["lines"]
    y1 = dict(IN["year1"], amortisation=IN["year1"]["amortization"])
    for line in ("sales", "cogs", "sga", "sbc", "depreciation", "amortisation", "capex"):
        assert ops[line]["first"] == y1[line]
    assert ops["sales"]["growth_from"] == IN["quarters"]["sales"]
    assert rb["operations"]["working_capital"]["prepaid"]["base"] == IN["quarters"]["cogs"]
    assert [p["periods"] for p in ops["depreciation"]["plus"] + ops["amortisation"]["plus"]] == \
        [IN["fv"]["ppe_life"], IN["fv"]["intangibles_life"]]
    assert ops["interest_income"]["rate"][1:] == IN["cash_rate"]
    fed, state = _d(IN["federal_tax_rate"]), _d(IN["state_tax_rate"])
    assert _d(rb["tax"]["rate"]) == fed * (1 - state) + state
    lens = CN["lenses"]["exit"]
    assert lens["period"] == EXIT and lens["multiple"] == IN["exit_multiple"]
    k = IN["kickers"]
    assert lens["ownership"]["layers"] == [{"subordinated_note": k["subordinated_note"], "preferred_a": k["preferred_a"]},
                                           {"management": k["management_pool"]}]
    for m, spec in CN["measures"].items():
        if spec["measure"] == "xirr":
            assert spec["dates"] == IN["dates"]


# ── the opening and the close ─────────────────────────────────────────────

def test_retained_earnings_balance_the_opening_to_the_copied_precision(res):
    acct, value = res.balancing["company"]
    assert acct == "retained_earnings"
    assert abs(float(value) - I["pre_deal"]["retained_earnings"]) < 1e-13


def test_the_case_runs_clean(res):
    assert res.failed_checks() == []


def test_the_deal_reproduces_the_workbooks_figures(res):
    deal = res.records("deal")[0]
    want = O["deal"]
    for k in ("offer_price", "fdso", "equity_purchase_price", "purchase_price", "goodwill"):
        assert float(deal[k]) == pytest.approx(want[k], abs=1e-12), k
    assert float(deal["marks"]["intangibles"]) == pytest.approx(want["intangibles_mark"], abs=1e-12)
    assert float(deal["dtl"]) == pytest.approx(want["dtl_created"], abs=1e-12)
    assert deal["offer_price"] == F("12.81") * F("1.25")
    assert float(deal["book_value"]) == pytest.approx(I["pre_deal"]["total_equity"], abs=1e-12)
    # sources: the sponsor closes the gap; the tax refund on costs and fees is a source
    for k in ("excess_cash", "option_proceeds", "term_loan_a", "senior_note", "subordinated_note",
              "sponsor_equity", "tax_refund"):
        found = {kk.split(".")[0] if "." in kk else kk: v for kk, v in deal["sources"].items()}
        alias = {"sponsor_equity": "sponsor", "tax_refund": "tax_refund"}.get(k, k)
        assert float(found[alias]) == pytest.approx(I["sources"][k], abs=1e-12), k
    assert float(sum(deal["uses"].values())) == pytest.approx(want["total_uses"], abs=1e-12)
    assert sum(deal["uses"].values()) == sum(deal["sources"].values())


# ── the statements, row by row ────────────────────────────────────────────

BS_ROWS = ["cash", "receivables", "prepaid", "ppe_gross", "accumulated_depreciation",
           "capitalised_financing_costs", "goodwill", "intangibles", "other_lt_assets",
           "payables", "accrued", "other_current_liabilities", "legacy_debt", "revolver", "term_loan_a",
           "senior_note", "subordinated_note", "net_dtl", "other_lt_liabilities", "preferred_a",
           "common_par", "apic", "retained_earnings"]
DEBIT_ROWS = {"cash", "receivables", "prepaid", "ppe_gross", "accumulated_depreciation",
              "capitalised_financing_costs", "goodwill", "intangibles", "other_lt_assets"}


@pytest.fixture(scope="module")
def bs(book):
    """The balance sheet rows, natural sign (assets positive), close then years 1..10."""
    scheme, pl = _scheme("balance_sheet"), _scheme("income_statement")
    out = {}
    for row in BS_ROWS:
        sign = -1 if row in DEBIT_ROWS else 1
        out[row] = [sign * x for x in balance_cut(book, scheme, [row]).values]
    # retained earnings are the account plus every income statement line closed into it
    earned = itertools.accumulate(pnl_cut(book, pl, sorted(set(pl.classes.values()))).values)
    out["retained_earnings"] = [a + b for a, b in zip(out["retained_earnings"], earned)]
    return out


@pytest.mark.parametrize("row", BS_ROWS)
def test_balance_sheet_row(bs, row):
    want = [O["close"][row]] + O["balance_sheet"][row]
    got = [float(x) for x in bs[row]]
    assert max(abs(a - b) for a, b in zip(got, want)) < 1e-9


def test_the_balance_sheet_balances_exactly_every_period(bs):
    for t in range(N):
        assets = sum(bs[r][t] for r in DEBIT_ROWS)
        claims = sum(bs[r][t] for r in BS_ROWS if r not in DEBIT_ROWS)
        assert assets == claims


IS_ROWS = {"sales": ("sales", 1), "cogs": ("cogs", -1), "sga": ("sga", -1), "depreciation": ("depreciation", -1),
           "amortization": ("amortisation", -1), "sbc": ("sbc", -1), "interest_on_cash": ("interest_income", -1),
           "revolver_interest": ("revolver_interest", -1), "term_loan_a_interest": ("term_loan_a_interest", -1),
           "senior_note_interest": ("senior_note_interest", -1),
           "subordinated_note_interest": ("subordinated_note_interest", -1),
           "financing_cost_amortisation": ("financing_cost_amortisation", -1), "tax": ("tax", -1)}


@pytest.mark.parametrize("row", sorted(IS_ROWS))
def test_income_statement_row(book, row):
    cls, sign = IS_ROWS[row]
    got = [sign * float(x) for x in pnl_cut(book, _scheme("income_statement"), [cls]).values]
    assert max(abs(a - b) for a, b in zip(got[1:], O["income_statement"][row])) < 1e-9


def test_net_income_and_the_preferred_dividend(book):
    pl = _scheme("income_statement")
    ni = pnl_cut(book, pl, sorted(set(pl.classes.values()) - {"deal"})).values
    assert max(abs(float(a) - b) for a, b in zip(ni[1:], O["income_statement"]["net_income"])) < 1e-9
    div = [mv(book, "preferred_a.dividends", t) for t in range(N)]
    assert [float(-d) for d in div[1:]]  # a dividend is a debit to equity == pytest.approx(O["income_statement"]["preferred_dividend"], abs=1e-12)


def test_the_cash_flow_statement(res, book):
    for name in ("operating", "investing", "financing"):
        got = [float(x) for x in res.cut(name).values[1:]]
        assert max(abs(a - b) for a, b in zip(got, O["cash_flow"][name])) < 1e-9, name
    cash = [book.balance("cash", t) for t in range(N)]
    change = [float(cash[t] - cash[t - 1]) for t in range(1, N)]
    assert max(abs(a - b) for a, b in zip(change, O["cash_flow"]["change_in_cash"])) < 1e-9
    # the three cuts are the whole of the change in cash after the close
    for t in range(1, N):
        assert sum(res.cut(n).values[t] for n in ("operating", "investing", "financing")) == cash[t] - cash[t - 1]


def test_term_loan_a_is_amortised_then_swept_in_year_one(res):
    paid = res.records("waterfall")[1]["paid"]
    assert (paid["term_loan_a.principal"], paid["term_loan_a.prepay"]) == (10, 40)
    assert -O["other"]["term_loan_a_mandatory"][0] == 10 and -O["other"]["term_loan_a_optional"][0] == 40


# ── the exit and the holders ──────────────────────────────────────────────

HOLDERS = {"subordinated_note": "subordinated_note", "preferred_a": "preferred_a",
           "sponsor": "sponsor_equity", "management": "management_equity"}


def test_the_exit_values(res):
    v = res.lenses["exit"]
    r = O["returns"]
    assert float(v["enterprise_value"]) == pytest.approx(r["enterprise_value"][EXIT], abs=1e-9)
    # the workbook's "net debt" row is cash less debt (it is added to EV); the lens's is debt less cash
    assert float(v["net_debt"]) == pytest.approx(-r["net_debt"][EXIT], abs=1e-9)
    assert float(v["equity_value"]) == pytest.approx(r["equity_value"][EXIT], abs=1e-9)
    assert v["enterprise_value"] == 5 * v["ebitda"]
    assert v["equity_value"] == v["enterprise_value"] - v["net_debt"]


def test_the_fully_diluted_ownership(res):
    own = res.lenses["exit"]["ownership"]
    w = O["ownership"]
    assert sum(own.values()) == 1
    assert float(own["sponsor"]) == pytest.approx(w["sponsor_equity"], abs=1e-15)
    assert float(own["subordinated_note"]) == pytest.approx(w["subordinated_note"], abs=1e-15)
    assert float(own["preferred_a"]) == pytest.approx(w["preferred_a"], abs=1e-15)
    # management holds its paid-in equity and the performance pool
    assert float(own["management"]) == pytest.approx(w["management_equity"] + w["management_pool"], abs=1e-15)


@pytest.mark.parametrize("holder", sorted(HOLDERS))
def test_holder_stream_irr_and_moic(res, holder):
    ref = HOLDERS[holder]
    stream = res.cut(f"exit.{holder}").values
    want = O["returns"][ref]
    assert max(abs(float(a) - b) for a, b in zip(stream[:EXIT + 1], want)) < 1e-9
    assert all(x == 0 for x in stream[EXIT + 1:])
    assert abs(res.measure(f"{holder}_irr") - O["irr"][ref]) < 1e-8
    assert float(res.measure(f"{holder}_moic")) == pytest.approx(O["cash_on_cash"][ref], abs=1e-12)


# ── findings: the workbook's behaviour, reproduced ────────────────────────

def _extra(res, book, line):
    """Each year's charge above the line's ratio to sales: the fair value mark's share."""
    lines = CASE["rulebooks"]["operations"]["lines"]
    ratio = F(_d(lines[line]["first"])) / F(_d(lines["sales"]["first"]))
    sales = [mv(book, "sales", t) for t in range(N)]
    return [mv(book, line, t) - ratio * sales[t] for t in range(N)]


def test_finding_the_marks_are_charged_only_after_the_typed_first_year(res, book):
    marks = res.records("deal")[0]["marks"]
    dep, amo = _extra(res, book, "depreciation"), _extra(res, book, "amortisation")
    assert dep[1] == amo[1] == 0                      # year 1 is typed without them
    assert sum(dep) == F(6, 7) * marks["ppe_gross"]   # 2.14 of the PP&E mark is never charged
    assert sum(amo) == F(4, 5) * marks["intangibles"]  # 21.89 of the intangibles mark is never charged
    assert float(dep[2]) == pytest.approx(O["other"]["writeup_depreciation"][1], abs=1e-12)


def test_finding_intangibles_go_negative_in_year_five():
    def floor(case):
        case["floors"]["company.intangibles"] = 0
    with pytest.raises(KernelError, match=r"'intangibles' .* in period 5"):
        variant(floor)


def test_finding_the_deferred_tax_on_the_marks_never_unwinds(res, book):
    dtl = res.records("deal")[0]["dtl"]
    fees = sum(CASE["contracts"][f"{n}_fee"]["terms"]["rate"] * c for n, c in
               [("revolver", 100), ("term_loan_a", 50), ("senior_note", 100), ("subordinated_note", 50)])
    fees = F(_d(fees)).limit_denominator(10**6)
    # opening, plus the marks' liability, plus the fees' (35%), less their unwinding at the forecast's 35.0003%
    want = F("-8.828") + dtl + fees * F("0.35") - fees * F("0.350003")
    assert book.balance("net_dtl", N - 1) == want
    assert float(fees * (F("0.35") - F("0.350003"))) == pytest.approx(-1.95e-5, abs=1e-12)


def test_finding_the_term_loan_a_fee_amortises_after_the_loan_is_repaid(book):
    assert all(book.balance("term_loan_a.debt", t) == 0 for t in range(1, N))
    charged = [mv(book, "term_loan_a_fee.amortisation", t) for t in range(N)]
    assert all(charged[t] == F(1, 5) for t in range(1, 6))


def test_finding_the_revolver_is_never_drawn(book):
    assert all(book.balance("revolver.debt", t) == 0 for t in range(N))
    assert all(book.balance("cash", t) >= 100 for t in range(1, N))


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_stated_retained_earnings_that_does_not_balance_refuses():
    def stated(case):
        case["opening"]["company"]["retained_earnings"]["value"] = I["pre_deal"]["retained_earnings"]
    with pytest.raises((KernelError, InputError), match="balance"):
        variant(stated)


def test_two_balancing_accounts_refuse():
    def two(case):
        case["opening"]["company"]["apic"]["value"] = "balancing"
    with pytest.raises(InputError, match="only one account may balance"):
        variant(two)


def test_a_bargain_purchase_refuses():
    def bargain(case):
        # 900 of marks, less their 35% deferred tax, exceed the 437.8 excess plus the 61.1 old goodwill
        case["rulebooks"]["deal"]["marks"] = [{"account": "ppe_gross", "amount": 900},
                                              {"account": "intangibles", "share_of_excess": 0}]
    with pytest.raises(InputError, match="bargain purchase"):
        variant(bargain)


def test_sources_beyond_the_uses_refuse():
    def flush(case):
        case["rulebooks"]["deal"]["sources"][4]["amount"] = 1000
    with pytest.raises(InputError, match="sources exceed uses"):
        variant(flush)


def test_a_mark_down_refuses():
    def down(case):
        case["rulebooks"]["deal"]["marks"][0]["amount"] = -5
    with pytest.raises(InputError, match="mark down"):
        variant(down)


def test_an_unknown_ratio_driver_refuses():
    def unknown(case):
        case["rulebooks"]["operations"]["lines"]["cogs"]["ratio_of"] = "revenue"
    with pytest.raises(InputError, match="ratio_of names 'revenue'"):
        variant(unknown)


def test_a_mark_from_an_undeclared_deal_refuses():
    def nodeal(case):
        case["rulebooks"]["operations"]["lines"]["depreciation"]["plus"][0]["mark"] = "merger.ppe_gross"
    with pytest.raises(InputError, match="no acquisition 'merger'"):
        variant(nodeal)


def test_a_mark_the_deal_does_not_make_refuses():
    def unmarked(case):
        case["rulebooks"]["deal"]["marks"] = case["rulebooks"]["deal"]["marks"][:1]
    with pytest.raises(InputError, match="acquisition 'deal' marks no 'intangibles'"):
        variant(unmarked)


def test_an_unknown_lens_kind_refuses():
    def unknown(case):
        case["lenses"]["exit"]["kind"] = "ipo_returns"
    with pytest.raises(InputError, match="unknown kind 'ipo_returns'"):
        variant(unknown)


def test_ownership_naming_a_non_holder_refuses():
    def stranger(case):
        case["lenses"]["exit"]["ownership"]["layers"][1] = {"advisor": 0.005}
    with pytest.raises(InputError, match="not holders"):
        variant(stranger)


def test_a_nil_tax_base_with_a_timing_register_refuses():
    def both(case):
        case["rulebooks"]["tax"]["timing"] = "fixed_assets"
        case["rulebooks"]["tax"]["scheme"]["depreciation"] = "timing"
    with pytest.raises(InputError, match="nil_tax_base"):
        variant(both)


def test_an_unread_key_refuses():
    def extra(case):
        case["rulebooks"]["deal"]["earn_out"] = 5
    with pytest.raises(InputError, match="read by nothing: earn_out"):
        variant(extra)
