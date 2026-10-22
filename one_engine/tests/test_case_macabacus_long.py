"""Macabacus long-form ("complex") LBO, financing case 6 "LBO C", rebuilt from its inputs (plan step 7g).

Referee: the workbook itself (``cases/reference/macabacus_lbo_long.json``,
cached values of macabacus-lbo-long.xlsx, written by
``consolidation/experiments/make_reference_macabacus_long.py``). One_engine
reads only its inputs (the balance sheet at the close before the deal, the
last quarter, the analyst projections, the cost savings, the offer, the
fair value marks, the financing terms and the period grid) and must
reproduce the close, every balance sheet and income statement row for the
stub and nine years, the cash flow statement, the deal figures, and the
four holders' returns (streams, XIRRs and cash-on-cash) at the year-4 exit.

Tolerances: as the short form (``test_case_macabacus.py``): rows to 1e-9 in
$ millions (largest difference seen 4.0e-13), IRRs to 1e-8 (Excel's XIRR
precision; largest difference seen 3.2e-9).

What the case adds to the engine (convergence count 1): ``measures.cash_on_cash``
(every later flow net over the first; ``moic`` counts the preferred
holder's payments in as money paid). Pack changes (not core), all in the
corporate pack: year fractions on fees, the preferred and the operating
plan (the stub); original issue discounts (``discounts``) and a prepayment
penalty in the acquisition; a discounted, paid-in-kind preferred; analyst
projections followed then held (``projected``), with cost savings
(``less``); working capital on an annualised quarter (``base_fraction``);
and, in the exit lens, an annualised EBITDA, prepayment premiums and
``interim_from``. The loan already took a per-period ``days_per_period``
and a ``principal_schedule``.

Not built, because the workbook's case 6 leaves them off: the alternative
minimum tax and the Section 382 limit on loss carry-forwards (both
switched off in the workbook), term loan B and the revolver's draws (no
deficit arises; tested), the legacy debt's conversion, and an average
balance for interest.

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
CASE_PATH = HERE / "macabacus_lbo_long.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "macabacus_lbo_long.json").read_text())
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


def close(got, want, tol=1e-9):
    return max(abs(float(a) - b) for a, b in zip(got, want, strict=True)) < tol


# ── the copy of the workbook's inputs ─────────────────────────────────────

def test_the_case_file_copies_the_workbooks_inputs():
    CN, IN = norm(CASE), norm(I)
    c, rb = CN["contracts"], CN["rulebooks"]
    stated = {k: v["value"] for k, v in CN["opening"]["company"].items()}
    assert stated.pop("retained_earnings") == "balancing"
    assert stated == {k: v for k, v in IN["standalone_at_close"].items()
                      if v and not k.startswith("total") and k != "retained_earnings"}
    fractions = [0] + IN["fractions"]
    for name, inst in IN["instruments"].items():
        if name in ("legacy_debt", "term_loan_b"):
            continue
        if name == "preferred_a":
            t = c[name]["terms"]
            assert (t["rate"], t["discount"], t["discount_years"]) == (inst["fixed"], inst["oid"], inst["oid_years"])
            assert t["pik_share"] == [0] + IN["pik"]["preferred_a"] and t["year_fraction"] == fractions
            continue
        t = c[name]["terms"]
        assert t["rate"][0] == 0 and t["days_per_period"] == fractions and t["day_count_basis"] == 1
        want = [_d(inst["fixed"]) if inst["fixed"] else _d(lb) + _d(inst["spread"]) for lb in IN["libor"]]
        assert [_d(x) for x in t["rate"][1:]] == want
        fee = c[f"{name}_fee"]["terms"]
        assert (fee["rate"], fee["years"], fee["year_fraction"]) == (inst["fee_rate"], inst["fee_years"], fractions)
        if inst["oid"]:
            oid = c[f"{name}_oid"]["terms"]
            assert (oid["facility"], oid["rate"], oid["years"]) == (name, inst["oid"], inst["oid_years"])
    assert c["revolver"]["terms"]["commitment"] == IN["revolver_commitment"]
    assert c["revolver"]["terms"]["commitment_fee_rate"][1:] == IN["commitment_fee_rate"]
    # term loan A: 20% of its amount a year, by year fraction, until repaid; the rest at its year-5 maturity
    tla, done, want = IN["instruments"]["term_loan_a"], Decimal(0), [Decimal(0)]
    for f in IN["fractions"]:
        share = min(tla["amortisation"] * f, 1 - done)
        done += share
        want.append(share * IN["sources"]["term_loan_a"])
    assert c["term_loan_a"]["terms"]["principal_schedule"] == want
    assert c["term_loan_a"]["terms"]["repay_periods"] == tla["bullet_year"]
    for note in ("senior_note", "subordinated_note"):
        assert c[note]["terms"]["repay_from"] == IN["instruments"][note]["bullet_year"]
    deal = rb["deal"]
    assert deal["offer"]["share_price"] == IN["share_price"] and deal["offer"]["premium"] == IN["offer_premium"]
    assert deal["offer"]["basic_shares"] == IN["basic_shares"] and deal["offer"]["options"] == IN["options"]
    assert deal["tax_rate"] == IN["tax_rate"] and deal["min_cash"] == IN["min_cash"]
    assert deal["prepayment_penalty"]["rate"] == IN["legacy_penalty_rate"]
    assert deal["marks"] == [{"account": "ppe_gross", "amount": IN["fv"]["ppe_mark"]},
                             {"account": "intangibles", "share_of_excess": IN["fv"]["intangibles_share"]}]
    items = deal["transaction_costs"]["items"]
    assert {k: items[k]["rate"] for k in ("advisory", "sponsor")} == IN["fee_rates"]
    assert {k: items[k] for k in ("legal", "accounting", "printing", "other")} == \
        {k: IN["uses"][f"{k}_fee"] for k in ("legal", "accounting", "printing", "other")}
    assert [s.get("amount") for s in deal["sources"][3:]] == [IN["sources"]["preferred_a"], IN["sources"]["management_equity"]]
    assert deal["sources"][3]["issue_cost"]["rate"] == IN["instruments"]["preferred_a"]["fee_rate"]
    plan = rb["operations"]
    assert plan["fractions"] == fractions
    ops = plan["lines"]
    proj = dict(IN["projections"], amortisation=IN["projections"]["amortization"])
    for line in ("sales", "cogs", "sga", "sbc", "depreciation", "amortisation", "capex"):
        assert ops[line]["projected"] == proj[line][1:4]       # columns M..O
    for line in ("cogs", "sga"):
        assert ops[line]["less"] == {"amount": IN["cost_savings"][line],
                                     "phase_in": [0] + IN["cost_savings"][f"{line}_phase_in"]}
    for acct, w in plan["working_capital"].items():
        assert (w["base"], w["base_fraction"]) == (IN["last_quarter"]["sales" if w["of"] == "sales" else "cogs"],
                                                   IN["last_quarter"]["fraction"])
    assert [p["years"] for p in ops["depreciation"]["plus"] + ops["amortisation"]["plus"]] == \
        [IN["fv"]["ppe_life"], IN["fv"]["intangibles_life"]]
    assert ops["interest_income"]["rate"][1:] == IN["cash_rate"]
    fed, state = _d(IN["federal_tax_rate"]), _d(IN["state_tax_rate"])
    assert _d(rb["tax"]["rate"]) == fed * (1 - state) + state
    lens = CN["lenses"]["exit"]
    assert lens["period"] == EXIT and lens["multiple"] == IN["exit_multiple"] and lens["fractions"] == fractions
    assert lens["holders"]["subordinated_note"]["premium"] == [0] + IN["prepayment_premiums"]["subordinated_note"]
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
    assert abs(float(value) - I["standalone_at_close"]["retained_earnings"]) < 1e-13


def test_the_case_runs_clean(res):
    assert res.failed_checks() == []


def test_the_deal_reproduces_the_workbooks_figures(res):
    deal = res.records("deal")[0]
    want = O["deal"]
    for k in ("offer_price", "fdso", "equity_purchase_price", "purchase_price", "goodwill"):
        assert float(deal[k]) == pytest.approx(want[k], abs=1e-12), k
    assert float(deal["marks"]["intangibles"]) == pytest.approx(want["intangibles_mark"], abs=1e-12)
    assert float(deal["dtl"]) == pytest.approx(want["dtl_created"], abs=1e-12)
    found = {k.split(".")[0]: v for k, v in deal["sources"].items()}
    alias = {"sponsor_equity": "sponsor", "management_equity": "management"}
    for k in ("excess_cash", "option_proceeds", "term_loan_a", "senior_note", "subordinated_note", "preferred_a",
              "sponsor_equity", "management_equity", "tax_refund"):
        assert float(found[alias.get(k, k)]) == pytest.approx(I["sources"][k], abs=1e-12), k
    for k in ("equity_purchase_price", "refinance", "oid", "prepayment_penalties", "financing_fees", "transaction_costs"):
        assert float(deal["uses"][k]) == pytest.approx(I["uses"][k], abs=1e-12), k
    # the discounts are 5% of the two notes and the preferred; the penalty 2% of the debt refinanced
    assert deal["uses"]["oid"] == F(5, 100) * (100 + 50 + 10) and deal["uses"]["prepayment_penalties"] == F(46, 10)
    assert sum(deal["uses"].values()) == sum(deal["sources"].values())
    assert float(sum(deal["uses"].values())) == pytest.approx(want["total_uses"], abs=1e-12)


def test_the_refund_deducts_the_penalty_and_all_fees_but_not_the_discounts(res):
    deal = res.records("deal")[0]
    u = deal["uses"]
    assert deal["sources"]["tax_refund"] == F("0.35") * (u["transaction_costs"] + u["prepayment_penalties"]
                                                         + u["financing_fees"])


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
    """The balance sheet rows, natural sign (assets positive), close then periods 1..10."""
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
    assert close(bs[row], [O["close"][row]] + O["balance_sheet"][row])


def test_the_balance_sheet_balances_exactly_every_period(bs):
    for t in range(N):
        assert sum(bs[r][t] for r in DEBIT_ROWS) == sum(bs[r][t] for r in BS_ROWS if r not in DEBIT_ROWS)


IS_ROWS = {"sales": ("sales", 1), "cogs": ("cogs", -1), "sga": ("sga", -1), "depreciation": ("depreciation", -1),
           "amortization": ("amortisation", -1), "sbc": ("sbc", -1), "interest_on_cash": ("interest_income", -1),
           "revolver_interest": ("revolver_interest", -1), "term_loan_a_interest": ("term_loan_a_interest", -1),
           "senior_note_interest": ("senior_note_interest", -1),
           "subordinated_note_interest": ("subordinated_note_interest", -1),
           "financing_cost_amortisation": ("financing_cost_amortisation", -1), "tax": ("tax", -1)}


@pytest.mark.parametrize("row", sorted(IS_ROWS))
def test_income_statement_row(book, row):
    cls, sign = IS_ROWS[row]
    got = [sign * x for x in pnl_cut(book, _scheme("income_statement"), [cls]).values]
    assert close(got[1:], O["income_statement"][row])


def test_net_income_and_the_preferred_dividend(book):
    pl = _scheme("income_statement")
    ni = pnl_cut(book, pl, sorted(set(pl.classes.values()) - {"deal"})).values
    assert close(ni[1:], O["income_statement"]["net_income"])
    div = [-mv(book, "preferred_a.dividends", t) for t in range(N)]      # a dividend is a debit to equity
    assert close(div[1:], O["income_statement"]["preferred_dividend"], 1e-12)


def test_the_cash_flow_statement(res, book):
    for name in ("operating", "investing", "financing"):
        assert close(res.cut(name).values[1:], O["cash_flow"][name]), name
    cash = [book.balance("cash", t) for t in range(N)]
    assert close([cash[t] - cash[t - 1] for t in range(1, N)], O["cash_flow"]["change_in_cash"])
    for t in range(1, N):
        assert sum(res.cut(n).values[t] for n in ("operating", "investing", "financing")) == cash[t] - cash[t - 1]


def test_the_schedules_behind_the_statements(book):
    """The workbook's supporting rows: discounts, fees, the preferred, the marks and the taxes."""
    other = O["other"]
    assert close([book.balance("senior_note_oid.deferred", t) for t in range(1, N)], other["senior_note_oid"])
    assert close([book.balance("subordinated_note_oid.deferred", t) for t in range(1, N)], other["subordinated_note_oid"])
    assert close([-book.balance("preferred_a.discount", t) for t in range(1, N)], other["preferred_a_oid"])
    oid = [mv(book, "senior_note_oid.amortisation", t) + mv(book, "subordinated_note_oid.amortisation", t)
           + mv(book, "preferred_a.discount", t) for t in range(1, N)]
    assert close(oid, other["oid_amortisation"])
    fees = [sum(mv(book, f"{x}_fee.amortisation", t) for x in ("revolver", "term_loan_a", "senior_note",
                                                                "subordinated_note")) for t in range(1, N)]
    assert close(fees, other["fee_amortisation"])
    assert close([book.balance("preferred_a.capital", t) for t in range(1, N)], other["preferred_a_face"])
    assert close([mv(book, "preferred_a.capital", t) for t in range(1, N)], other["preferred_a_pik"])
    for note in ("senior_note", "subordinated_note"):
        assert close([book.balance(f"{note}.debt", t) for t in range(1, N)], other[f"{note}_face"])
        assert close([mv(book, f"{note}.interest", t) for t in range(1, N)], other[f"{note}_cash_interest"])
    assert close([mv(book, "revolver.fees", t) for t in range(1, N)], other["revolver_fee"])
    assert close([mv(book, "tax_expense", t) for t in range(1, N)], other["cash_tax"])
    assert close([mv(book, "deferred_tax_expense", t) for t in range(1, N)], other["deferred_tax"])


def test_term_loan_a_is_amortised_then_swept_in_the_stub(res):
    paid = res.records("waterfall")[1]["paid"]
    assert (paid["term_loan_a.principal"], paid["term_loan_a.prepay"]) == (F(15, 2), F(85, 2))
    assert -O["other"]["term_loan_a_mandatory"][0] == pytest.approx(7.5, abs=1e-12)
    assert -O["other"]["term_loan_a_optional"][0] == pytest.approx(42.5, abs=1e-12)


# ── the exit and the holders ──────────────────────────────────────────────

HOLDERS = {"subordinated_note": "subordinated_note", "preferred_a": "preferred_a",
           "sponsor": "sponsor_equity", "management": "management_equity"}


def test_the_exit_values(res):
    v = res.lenses["exit"]
    o = O["other"]
    assert float(v["enterprise_value"]) == pytest.approx(o["enterprise_value"][EXIT - 1], abs=1e-9)
    # the workbook's "net debt" row is cash less debt (it is added to EV); the lens's is debt less cash
    assert float(v["net_debt"]) == pytest.approx(-o["net_debt"][EXIT - 1], abs=1e-9)
    assert float(v["equity_value"]) == pytest.approx(o["equity_value"][EXIT - 1], abs=1e-9)
    assert v["equity_value"] == v["enterprise_value"] - v["net_debt"]


@pytest.mark.parametrize("period", range(1, N))
def test_the_exit_values_at_every_period_annualise_the_stub(period):
    """The workbook values every period; the stub's EBITDA (three quarters) is annualised."""
    def at(case):
        case["lenses"]["exit"]["period"] = period
    v = variant(at).lenses["exit"]
    o = O["other"]
    assert float(v["enterprise_value"]) == pytest.approx(o["enterprise_value"][period - 1], abs=1e-9)
    assert float(v["net_debt"]) == pytest.approx(-o["net_debt"][period - 1], abs=1e-9)
    assert float(v["ebitda"]) == pytest.approx(O["income_statement"]["ebitda"][period - 1], abs=1e-9)
    if period == 1:
        assert v["enterprise_value"] == 5 * v["ebitda"] / F(3, 4)


def test_the_fully_diluted_ownership(res):
    own = res.lenses["exit"]["ownership"]
    w = O["ownership"]
    assert sum(own.values()) == 1
    for h in ("sponsor", "subordinated_note", "preferred_a"):
        assert float(own[h]) == pytest.approx(w[HOLDERS[h]], abs=1e-15)
    # management holds its paid-in equity and the performance pool
    assert float(own["management"]) == pytest.approx(w["management_equity"] + w["management_pool"], abs=1e-15)


@pytest.mark.parametrize("holder", sorted(HOLDERS))
def test_holder_stream_irr_and_cash_on_cash(res, holder):
    ref = HOLDERS[holder]
    stream = res.cut(f"exit.{holder}").values
    assert close(stream, O["returns"][ref])
    assert abs(res.measure(f"{holder}_irr") - O["irr"][ref]) < 1e-8
    assert res.measure(f"{holder}_cash_on_cash") == pytest.approx(O["cash_on_cash"][ref], abs=1e-12)


def test_the_subordinated_note_is_repaid_with_its_prepayment_premium(res, book):
    stream = res.cut("exit.subordinated_note").values
    share = res.lenses["exit"]["ownership"]["subordinated_note"] * res.lenses["exit"]["equity_value"]
    interest = mv(book, "subordinated_note.interest", EXIT)
    assert stream[EXIT] == interest + 50 * (1 + F(1, 100)) + share


# ── findings: the workbook's behaviour, reproduced ────────────────────────

def test_finding_the_preferred_holder_pays_in_while_the_dividend_is_paid_in_kind(res, book):
    """The PIK share is of the dividend booked, discount amortisation included, so it exceeds the coupon."""
    stream = res.cut("exit.preferred_a").values
    assert stream[2] == stream[3] == F(-1, 8)                       # the discount's 0.5 over 4 years
    accreted = [mv(book, "preferred_a.capital", t) for t in range(N)]
    booked = [-mv(book, "preferred_a.dividends", t) for t in range(N)]
    assert all(accreted[t] == booked[t] for t in (1, 2, 3))         # the whole dividend booked is accreted
    assert accreted[4] == booked[4] / 4 and accreted[5] == 0


def test_finding_the_stub_interim_flows_are_left_out_of_the_returns(res, book):
    """The workbook's returns rows skip the stub column (its header is text); the coupons were paid."""
    assert mv(book, "subordinated_note.interest", 1) == 50 * F("0.1025") * F(3, 4)
    assert res.cut("exit.subordinated_note").values[1] == 0

    def every(case):
        del case["lenses"]["exit"]["interim_from"]
    counted = variant(every)
    assert counted.cut("exit.subordinated_note").values[1] == 50 * F("0.1025") * F(3, 4)
    assert counted.measure("subordinated_note_irr") > res.measure("subordinated_note_irr") + 0.01


def test_finding_the_notes_are_invested_at_face_though_the_company_received_less(res, book):
    """The sub note's stream starts at -50, its face; 2.5 of it (the 5% discount) was withheld at the close."""
    assert res.cut("exit.subordinated_note").values[0] == -50
    assert book.balance("subordinated_note_oid.deferred", 0) == F(5, 2)

    def net(case):
        case["schemes"]["holders"]["subordinated_note_oid.deferred"] = "subordinated_note"
    assert variant(net).cut("exit.subordinated_note").values[0] == F(-95, 2)
    # the preferred's stream, by contrast, starts net of its discount
    assert res.cut("exit.preferred_a").values[0] == F(-19, 2)


def test_finding_net_debt_is_at_book_value_while_the_holders_are_repaid_at_face(res, book):
    v = res.lenses["exit"]
    debt_book = sum(book.balance(f"{x}.debt", EXIT) - book.balance(f"{x}_oid.deferred", EXIT)
                    for x in ("senior_note", "subordinated_note"))
    pref_book = book.balance("preferred_a.capital", EXIT) + book.balance("preferred_a.discount", EXIT)
    assert v["net_debt"] == debt_book + pref_book - book.balance("cash", EXIT)
    unamortised = book.balance("subordinated_note_oid.deferred", EXIT)
    assert unamortised == F(5, 32)                                  # the sub note is valued 0.156 below face
    # and repaid at face plus 1%: equity value omits 0.156 + 0.5 of what the sub holder takes


def test_finding_the_year_four_projection_is_typed_but_not_used(res, book):
    """Column P projects year 4 sales flat at 474.7; the workbook grows them at year 3's rate instead."""
    assert I["projections"]["sales"][4] == I["projections"]["sales"][3] == 474.7
    assert float(mv(book, "sales", 4)) == pytest.approx(O["income_statement"]["sales"][3], abs=1e-12)
    assert mv(book, "sales", 4) == F("474.7") * F("474.7") / F("470.5")

    def use_p(case):
        case["rulebooks"]["operations"]["lines"]["sales"]["projected"].append(Decimal("474.7"))
    assert mv(variant(use_p).group.book("company"), "sales", 4) == F("474.7")


def test_finding_the_marks_are_charged_in_full_from_the_stub(res, book):
    """Unlike the short form, whose typed first year leaves part of each mark uncharged."""
    marks = res.records("deal")[0]["marks"]
    plus = {"depreciation": "writeup_depreciation", "amortisation": "writeup_amortisation"}
    for line, row in plus.items():
        lines = CASE["rulebooks"]["operations"]["lines"]
        proj, sales = lines[line]["projected"], lines["sales"]["projected"]
        ratio = [F(_d(p)) / F(_d(s)) for p, s in zip(proj, sales)]
        extra = [mv(book, line, t) - ratio[min(t - 1, 2)] * mv(book, "sales", t) for t in range(1, N)]
        assert close(extra, O["other"][row], 1e-12)
        assert sum(extra) == marks["ppe_gross" if line == "depreciation" else "intangibles"]


def test_finding_intangibles_go_negative_in_year_four():
    def floor(case):
        case["floors"]["company.intangibles"] = 0
    with pytest.raises(KernelError, match=r"'intangibles' .* in period 4"):
        variant(floor)


def test_finding_the_term_loan_a_fee_amortises_after_the_loan_is_repaid(book):
    assert all(book.balance("term_loan_a.debt", t) == 0 for t in range(1, N))
    charged = [mv(book, "term_loan_a_fee.amortisation", t) for t in range(N)]
    assert charged[1] == F(3, 20) and all(charged[t] == F(1, 5) for t in range(2, 6))
    assert charged[6] == F(1, 20) and sum(charged) == 1


def test_finding_the_revolver_is_never_drawn(book):
    assert all(book.balance("revolver.debt", t) == 0 for t in range(N))
    assert all(book.balance("cash", t) >= 100 for t in range(1, N))


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_projected_cost_driven_by_an_unprojected_line_refuses():
    def unprojected(case):
        case["rulebooks"]["operations"]["lines"]["cogs"]["ratio_of"] = "interest_income"
    with pytest.raises(InputError, match="ratio_of names 'interest_income', not a projected line"):
        variant(unprojected)


def test_a_projected_growth_line_with_one_projection_refuses():
    def one(case):
        case["rulebooks"]["operations"]["lines"]["sales"]["projected"] = [351]
    with pytest.raises(InputError, match="needs two periods to show a growth"):
        variant(one)


def test_an_unread_key_on_a_savings_rule_refuses():
    def extra(case):
        case["rulebooks"]["operations"]["lines"]["cogs"]["less"]["from"] = 2
    with pytest.raises(InputError, match="from"):
        variant(extra)
