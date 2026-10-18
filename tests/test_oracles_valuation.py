"""McKinsey *Valuation*'s exhibits through the oracle harness, and the reader that reads them.

The book's exhibits are read mechanically (:mod:`one_engine.oracles.valuation`),
so each way a translation could pass one dishonestly is pinned here: a
printed figure no row reads and no set-aside line explains; a row that takes
one column of a run and leaves the other unread; a number typed into the
case; a row declared but neither fed, compared nor set aside; a set-aside line
the excerpt does not print. Each is planted on a copy of a real translation
and must fail.

Then the excerpts are held to the book: each vendored excerpt is, byte for
byte, the book lines its manifest origin names (wherever the book is
present), and an excerpt is never rewritten. The book map finds every
chapter and gives every exhibit the chapter its number says.

Last, every translated exhibit gets its recorded verdict, with the planted
error caught.
"""
import copy
import json
from fractions import Fraction as F

import pytest
import yaml

from one_engine import oracles as O
from one_engine.cases import run_case
from one_engine.cuts import CutError
from one_engine.oracles import valuation as VO
from one_engine.oracles.valuation import ValuationFamily, figures_of, read_line, read_row

FAMILY = ValuationFamily()
ORACLES = FAMILY.oracles()
#: the worked examples, built by hand before the queue, and their number of checks (one per figure row,
#: plus the family's own); every other translated exhibit is held to the same verdict without a count
WORKED = {"7.1-3": 58, "7.4-5@home_depot": 40, "7.4-5@lowes": 40}
#: the cells where the book's printed figure cannot be reproduced from what it prints, by more than one
#: unit of its last digit: oracle -> {"<row> at <period>"}. The translation compares the line as the
#: book means it, so the receipt is near or differs on exactly these and on nothing else; each entry
#: says what does not add up. A check reports its first failing period, so an entry names that cell.
BOOK_SLIPS = {
    # 9.5's 2010 EBITA / revenues prints 35.8%, 2009's value again; its own 2010 EBITA and revenues
    # give 102.3 / 288.0 = 35.5%, and the text (p. 200) quotes EBITA at 103.2, which the table does not print
    "9.5-7+9.10-12": {"ebita_ratio at t=2"},
    # 14.1's revenue-multiple deviation (sample stdev over median) prints 38.1; the one-decimal multiples
    # it prints give 38.5. Not an arithmetic slip: the book worked from unrounded multiples near 1
    "14.1": {"deviation_ev_revenue at t=0"},
    # 14.4's EV-to-EBITA row prints 5.0 in every column; EBITA (EBIT plus amortization) gives 7.5
    # (1,125 / 150, 375 / 50, 1,500 / 200). 5.0 is EV over EBITDA
    "14.4": {"ev_to_ebita_a at t=0", "ev_to_ebita_b at t=0", "ev_to_ebita_c at t=0", "ev_to_ebita_ab at t=0",
             "ev_to_ebita_c_after at t=0"},
    # 14.7's company C prints 9.0 for debt plus equity minus cash over EBITA; (300 + 500 - 0) / 100 = 8.0,
    # as the next row prints. 9.0 is what it would be with the minority interest added
    "14.7": {"dpe_less_cash_to_ebita_c at t=0"},
    # 29.7's year-25 perpetuity value prints 31,064; its own FCF, growth and rate give 31,066.2, which is
    # also the real-terms value indexed to nominal, as the book says it should be. A rounded-input gap
    "29.6-7": {"cv_perpetuity_n25 at t=25"},
    # 30.1's real cash flows deflate each year's nominal flow by that year's inflation only (106.6 / 1.01
    # = 105.5, printed 105.6), not by the price index compounded from the first year (105.0); every year
    # from 2011 on, in both currencies, and the check names the first
    "30.1": {"chf_real_cash_flow at t=2", "eur_real_cash_flow at t=2"},
    # 30.8's adjusted foreign-exchange gain reads 201: the extraction glues footnote mark 1 to the 20 the
    # exhibit means (its dollar cell is 20 x 0.85 = 17)
    "30.8": {"fx_gain_adj at t=0"},
    # input rounding, not arithmetic slips: the book worked from unrounded figures the exhibit prints rounded.
    # 27.1-6's year-3 NOPLAT 101.1 and operating taxes 43.6 come out at 101.2 and 43.5 from the printed lines
    "27.1-6": {"u_noplat at t=3", "u_rec_noplat at t=3", "u_fcf_noplat at t=3", "a_operating_taxes at t=3"},
    # 28.4's ending balance prints 23.4; its own printed cells give 23.3
    "28.4": {"ending_early at t=2"},
    # 28.5-8's EBITA and NOPLAT print 973 and 773 against 971.9 and 771.9 from the printed lines; its ROICs
    # divide by capital printed to the unit (4 to 435), too coarse for a ROIC printed to a tenth (12,707.7%
    # against 11,350% on a capital of 4; every period of that row), and 621 / 1,751 is 35.5, printed 35.4
    "28.5-8": {"a_ebita at t=1", "a_noplat at t=1", "fa_noplat at t=1", "roic_expensed_ex_goodwill at t=0",
               "roic_expensed at t=0", "roic_capitalized at t=0"},
}
BOOK = VO.book_path()
needs_book = pytest.mark.skipif(BOOK is None, reason="the valuation book is not in this checkout's shared folder")


# ── the reader ──────────────────────────────────────────────────────────────

def test_a_printed_number_is_read_with_its_sign_and_precision():
    assert VO._number("9,790") == (9790, 0)
    assert VO._number("(1,027)") == (-1027, 0)
    assert VO._number("37.6%") == (F(376, 10), 1)
    assert VO._number("$50") == (50, 0)
    assert VO._number("(12") is None and VO._number("taxes1") is None
    assert VO._number("2009.") == (2009, 0) and VO._number("28,") == (28, 0)    # a number ending a sentence


def test_glued_dashes_are_one_nil_cell_each():
    assert VO._cells("–––") == [(0, 0)] * 3
    positions, cells, _ = read_row("Goodwill ––– 15\n", "Goodwill")
    assert cells == [(0, 0)] * 3 + [(15, 0)]
    assert positions == [(0, 1, 0), (0, 1, 1), (0, 1, 2), (0, 2, 0)]


def test_a_footnote_mark_opening_a_line_is_not_a_figure():
    text = "1 Assumes a marginal tax of 25% on all income.\n"
    assert [cells for _li, _ti, cells in figures_of(text)] == [[(25, 0)]]


def test_only_a_bare_small_integer_is_a_footnote_mark():
    text = "67% probability of success 1,954\n$50 per share\n2007 was a good year\n12 Footnote\n"
    assert [cells for _li, _ti, cells in figures_of(text)] == [[(67, 0)], [(1954, 0)], [(50, 0)], [(2007, 0)]]


def test_a_label_is_read_at_its_occurrence_and_never_inside_another_word():
    text = "Interest-bearing debt 225 200\nInterest (20) Operating taxes1 (70)\nInterest 5\n"
    assert read_row(text, "Interest")[1] == [(-20, 0)]
    assert read_row(text, "Interest", occurrence=2)[1] == [(5, 0)]
    with pytest.raises(KeyError):
        read_row(text, "Interest", occurrence=3)


def test_a_row_broken_over_lines_reads_on_only_through_lines_of_numbers():
    text = "Free cash flow –\n–\n11.9 12.6\nNOPLAT 5\n"
    assert read_row(text, "Free cash flow", continued=2)[1] == [(0, 0), (0, 0), (F(119, 10), 1), (F(126, 10), 1)]
    with pytest.raises(KeyError, match="not numbers only"):
        read_row(text, "Free cash flow", continued=3)


def test_a_line_is_read_by_its_number_whatever_words_sit_between_its_figures():
    text = "Some chart\n67 60\n$893 $893\n67% probability of success 1,954\n1 Assumes 25% tax\n"
    assert read_line(text, 3)[1] == [(893, 0), (893, 0)]
    assert read_line(text, 4) == ([(3, 0, 0), (3, 4, 0)], [(67, 0), (1954, 0)])
    assert read_line(text, 5)[1] == [(25, 0)]                     # the footnote mark is not a figure
    with pytest.raises(KeyError, match="prints no figure"):
        read_line(text, 1)
    with pytest.raises(KeyError):
        read_line(text, 9)


def test_numbers_inside_a_label_are_the_labels_own():
    positions, cells, in_label = read_row("Debt-to-equity in 2006 0.26 0.16\n", "Debt-to-equity in 2006")
    assert cells == [(F(26, 100), 2), (F(16, 100), 2)] and in_label == [(0, 2, 0)]


# ── a translation cannot pass dishonestly ───────────────────────────────────

def _tr(oid="7.1-3"):
    return yaml.safe_load((VO.TRANSLATIONS / f"{oid}.yaml").read_text())


def _run_with(tmp_path, tr, oid="7.1-3", plant=False):
    (tmp_path / f"{oid}.yaml").write_text(yaml.safe_dump(tr, sort_keys=False))
    checks, _ = ValuationFamily(translations=tmp_path).run(oid, plant)
    return {c.name: c for c in checks}


def test_the_worked_example_passes_as_translated(tmp_path):
    checks = _run_with(tmp_path, _tr())
    assert all(c.ok for c in checks.values()), [c for c in checks.values() if not c.ok]


def test_a_printed_figure_no_row_reads_fails(tmp_path):
    tr = _tr()
    del tr["rows"]["free_cash_flow"], tr["figures"]["free_cash_flow"]
    c = _run_with(tmp_path, tr)["every printed figure accounted for"]
    assert not c.ok and "160" in c.got


def test_a_run_of_lines_is_read_as_one_row():
    text = "Bars\n12%\n49% 50%\nAverage 37%\n"
    rows, claimed, problems, _ = VO._read_rows({"periods": [0, 1, 2], "rows": {"bars": {"line": [2, 3]}}},
                                               text, "test")
    assert problems == [] and rows["bars"]["cells"] == [(12, 0), (49, 0), (50, 0)]
    assert claimed == {(1, 0, 0), (2, 0, 0), (2, 1, 0)}


def test_a_row_that_takes_one_year_of_a_run_leaves_the_other_unaccounted(tmp_path):
    tr = _tr()
    del tr["rows"]["inventory"], tr["figures"]["inventory"]      # inventory_prior takes 200, nobody 225
    c = _run_with(tmp_path, tr)["every printed figure accounted for"]
    assert not c.ok and "225" in c.got


def test_a_label_holding_a_figure_is_accounted_for_but_shown_in_the_receipt(tmp_path):
    # the translation reads 7.2's revenue line with the figure inside its label: accounted for, and listed
    tr = _tr()
    tr["rows"]["revenues"] = {"label": "Revenues 1,000 Revenues", "periods": [1]}
    (tmp_path / "7.1-3.yaml").write_text(yaml.safe_dump(tr, sort_keys=False))
    checks, record = ValuationFamily(translations=tmp_path).run("7.1-3")
    assert all(c.ok for c in checks)
    assert record["labels_with_numbers"] == ["Revenues 1,000 Revenues"]


def test_a_number_typed_into_the_case_fails(tmp_path):
    tr = _tr()
    tr["case"]["generators"]["book"]["values"]["tax_due"] = "-(0.25 * (revenues + operating_costs))"
    c = _run_with(tmp_path, tr)["translation traced"]
    assert not c.ok and "0.25" in c.got


def test_the_days_of_a_year_may_be_typed_and_nothing_near_them(tmp_path):
    tr = _tr()
    tr["case"]["generators"]["book"]["values"]["days"] = "365"
    assert _run_with(tmp_path, tr)["translation traced"].ok
    tr["case"]["generators"]["book"]["values"]["days"] = "360"
    assert not _run_with(tmp_path, tr)["translation traced"].ok


def test_a_row_neither_fed_compared_nor_set_aside_fails(tmp_path):
    tr = _tr()
    del tr["figures"]["noplat"]
    assert _run_with(tmp_path, tr)["noplat"].got == "not reported"


def test_a_figure_mapped_to_no_row_and_a_stale_set_aside_fail(tmp_path):
    tr = _tr()
    tr["figures"]["goodwill"] = {"balance": "net_ppe"}
    tr["set_aside"]["EXHIBIT 7.9 Not in this excerpt"] = "no such line"
    checks = _run_with(tmp_path, tr)
    assert not checks["figure map"].ok and "goodwill" in checks["figure map"].got
    assert not checks["rows read"].ok and "7.9" in checks["rows read"].got


def test_a_wrong_figure_is_caught(tmp_path):
    # the translation books the cash flow statement's inventory line on the wrong side
    tr = _tr()
    tr["case"]["rulebooks"]["year"]["moves"]["inventory"] = {
        "debit": "inventory", "credit": "cash", "amount": {"generator": "book", "output": "inventory_change"}}
    checks = _run_with(tmp_path, tr)
    assert not checks["inventory"].ok and checks["inventory"].got == "175"
    assert not checks["cash_flow_from_operations"].ok and checks["cash_flow_from_operations"].got == "268"
    # invested capital does not see it: the 50 lands in cash, which this translation classifies as
    # operating, so only the rows that read inventory or cash on their own catch the slip
    assert checks["invested_capital"].ok


def test_the_plant_moves_the_books_and_not_the_excerpt(tmp_path):
    before = (O.REFERENCE / FAMILY.source("7.1-3")).read_bytes()
    checks = _run_with(tmp_path, _tr(), plant=True)
    assert not checks["noplat"].ok and checks["noplat"].got == "217.5"
    assert (O.REFERENCE / FAMILY.source("7.1-3")).read_bytes() == before


def test_the_other_companys_columns_count_only_as_its_own_translation_reads_them(tmp_path):
    # Home Depot's translation alone, without Lowe's beside it: Lowe's columns are unaccounted
    hd = _tr("7.4-5@home_depot")
    checks = _run_with(tmp_path, hd, oid="7.4-5@home_depot")
    assert not checks["every printed figure accounted for"].ok
    assert "columns_of names 'lowes'" in checks["rows read"].got
    # with Lowe's translation beside it, Lowe's rows account for them
    (tmp_path / "7.4-5@lowes.yaml").write_text((VO.TRANSLATIONS / "7.4-5@lowes.yaml").read_text())
    checks = _run_with(tmp_path, hd, oid="7.4-5@home_depot")
    assert checks["every printed figure accounted for"].ok and "rows read" not in checks


def test_a_balance_sheet_that_does_not_balance_moves_the_plug(tmp_path):
    # the planted run raises Home Depot's inventories by 1%: the stated books no longer balance
    checks = _run_with(tmp_path, _tr("7.4-5@home_depot"), oid="7.4-5@home_depot", plant=True)
    assert not checks["books"].ok and "the stated balances balance" in checks["books"].got
    assert not checks["total_assets"].ok and checks["total_liabilities_and_equity"].ok


def test_a_plant_on_a_small_percent_moves_beyond_the_tolerance(tmp_path):
    # the footnote's 25% moved by 1% would be 25.25%, inside the one-unit tolerance of a printed 25%;
    # the plant moves it ten units (to 35%), and the computed taxes move with it
    tr = _tr()
    tr["plant"] = "marginal_tax"
    checks = _run_with(tmp_path, tr, plant=True)
    assert not checks["taxes"].ok and checks["taxes"].got == "-92.4"


# ── the lens refuses books it cannot read ───────────────────────────────────

def _case_of(tr):
    case = copy.deepcopy(tr["case"])
    rows = {"inventory_prior": "200", "inventory_prior__0": "200"}
    case["generators"] = {"book": {"kind": "formulas", "periods": 2, "inputs": rows, "values": {}}}
    case["opening"] = {"company": {"inventory": {"nature": "asset", "value": {"generator": "book",
                                                                             "output": "inventory_prior__0"}},
                                   "common_stock": {"nature": "equity", "value": "balancing"}}}
    case["rulebooks"] = {}
    case["cuts"] = {}
    case["schemes"] = {"valuation": {"inventory": "operating_current_asset", "common_stock": "equity"}}
    case["lenses"]["v"]["marginal_tax_rate"] = 0
    return case


def test_the_lens_reads_a_classified_book():
    r = run_case(_case_of(_tr()))
    assert r.cut("v.invested_capital").values == [200, 200]
    assert r.cut("v.total_funds_sources").values == [200, 200]


def test_roic_before_there_is_capital_is_named_undefined_not_zero():
    # the business is funded in period 1: period 0 has no capital, and the books open on nothing
    case = _case_of(_tr())
    del case["opening"]
    case["generators"]["book"]["inputs"] = {"funding": ["0", "200"]}
    case["rulebooks"] = {"fund": {"kind": "transfer", "entity": "company", "phase": "operate",
                                  "declare": {"inventory": "asset", "common_stock": "equity"},
                                  "moves": {"fund": {"debit": "inventory", "credit": "common_stock",
                                                     "amount": {"generator": "book", "output": "funding"}}}}}
    case["lenses"]["v"]["roic_on"] = "opening"
    r = run_case(case)
    assert r.lenses["v"]["roic_undefined"] == [0, 1]       # opening capital is nil in both periods
    assert r.cut("v.roic").values == [0, 0]
    case["lenses"]["v"]["roic_on"] = "closing"
    assert run_case(case).lenses["v"]["roic_undefined"] == [0]


def test_the_lens_refuses_an_unclassified_account():
    case = _case_of(_tr())
    del case["schemes"]["valuation"]["inventory"]
    with pytest.raises(CutError, match="does not classify: inventory"):
        run_case(case)


def test_the_lens_refuses_a_class_it_does_not_know():
    case = _case_of(_tr())
    case["schemes"]["valuation"]["inventory"] = "operating_currnet_asset"
    with pytest.raises(CutError, match="does not know: operating_currnet_asset"):
        run_case(case)


# ── the excerpts are the book's lines ───────────────────────────────────────

@needs_book
@pytest.mark.parametrize("excerpt", FAMILY.excerpts())
def test_each_excerpt_is_the_book_lines_its_manifest_names(excerpt):
    lines = VO.read_book()
    text = (O.REFERENCE / VO.FOLDER / f"{excerpt}.txt").read_text(encoding="utf-8")
    assert text == VO.cut_excerpt(lines, VO.excerpt_ranges(excerpt))


def test_an_excerpt_is_never_rewritten(tmp_path):
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(json.dumps({"files": {}}))
    lines = ["EXHIBIT 1.1 A", "Revenue 10"]
    VO.vendor_excerpt("x", [(1, 2)], lines=lines, root=tmp_path, manifest=manifest)
    assert (tmp_path / "valuation" / "x.txt").read_text() == "EXHIBIT 1.1 A\nRevenue 10\n"
    assert VO.excerpt_ranges("x", manifest) == [(1, 2)]
    with pytest.raises(O.OracleChanged, match="never rewritten"):
        VO.vendor_excerpt("x", [(1, 1)], lines=lines, root=tmp_path, manifest=manifest)


def test_a_book_whose_hash_moved_refuses(tmp_path):
    fake = tmp_path / "book.txt"
    fake.write_text("EXHIBIT 7.1 An Example of Invested Capital\n")
    with pytest.raises(O.OracleChanged, match="is not the book's"):
        VO.read_book(fake)


@needs_book
def test_the_book_map_finds_every_chapter_and_places_every_exhibit():
    lines = VO.read_book()
    assert len(VO.chapter_starts(lines)) == len(VO.CHAPTERS) == 41
    exhibits = VO.book_map(lines)["exhibits"]
    # every chapter's exhibits run 1..n with none missing and none twice: 384 in the book
    ids = [e["id"] for e in exhibits]
    assert len(ids) == len(set(ids)) == 384
    for ch in {e["chapter"] for e in exhibits}:
        nums = sorted(int(e["id"].split(".")[1]) for e in exhibits if e["chapter"] == ch)
        assert nums == list(range(1, len(nums) + 1)), ch
    # an exhibit's number names its chapter; the page the running heads give it must agree
    assert [e["id"] for e in exhibits if VO.chapter_of(e["page"]) != e["chapter"]] == []


# ── every translated exhibit gets its recorded verdict ──────────────────────

def test_every_translation_reads_a_vendored_excerpt():
    assert set(WORKED) <= set(ORACLES) and set(BOOK_SLIPS) <= set(ORACLES)
    names = {p.stem for p in VO.TRANSLATIONS.glob("*.yaml")}
    assert names <= set(ORACLES), sorted(names - set(ORACLES))


@pytest.mark.parametrize("oid", ORACLES)
def test_every_exhibit_matches_with_its_plant_caught(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    if FAMILY.translation(oid) is None:
        assert r.verdict == "blocked" and "no translation yet" in r.reason
        return
    failing = {f"{c['name']} at {c['at']}" for c in r.checks if not c["ok"]}
    if oid in BOOK_SLIPS:
        assert r.verdict in ("near", "differs") and failing == BOOK_SLIPS[oid], failing
    else:
        assert r.verdict == "matches", r.reason
    assert r.planted_caught and r.n_checks > 3
    if oid in WORKED:
        assert r.n_checks == WORKED[oid]
