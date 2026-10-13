"""The numpy-financial and pyxirr test suites run through the oracle harness, and the reader that reads them.

The reader is the only thing authored here (there is no translation), so the
ways it could pass a case dishonestly are each pinned:

* a refusal never satisfies a numeric answer, and several roots are a
  refusal even when the library's pick is among them;
* a test that calls a function the engine lacks is blocked, naming it;
* tolerances, flows and dates come from the vendored files (the default
  ``assert_almost_eq!`` tolerance from ``helpers.rs``, quoted CSV dates as
  ``csv.reader`` reads them).

Then every oracle gets its recorded verdict. The differences are of three
kinds, all checked rather than listed: the engine refuses a stream with
several rates where the library picks one (every such refusal must name the
library's rate among its roots); two pyxirr samples where the library
finds no rate and the engine finds one (its XNPV must change sign across it);
and two pyxirr rate tests with two rates, where every rate the engine names
must pass the test's own future-value check.
"""
import pytest

from one_engine import oracles as O
from one_engine.measures import fv, xnpv
from one_engine.oracles.library import (EXCLUDED, HELPERS, NUMPY, PYXIRR, LibraryFamily, Refused, _csv,
                                        _default_eps, _future_value_eps, _literal, _near, _numpy_tests,
                                        _refuses, _run_numpy, _run_pyxirr)

FAMILY = LibraryFamily()
ORACLES = FAMILY.oracles()
#: pyxirr samples where pyxirr finds no rate (NaN) but a rate exists: the engine's root, at which the
#: XNPV changes sign; pyxirr's solver misses it. The oracle still wins: these stay ``differs``
SOLVER_MISSES = {"pyxirr/test_scheduled::test_xirr_samples[case_30_20]",
                 "pyxirr/test_scheduled::test_xirr_samples[case_30_45]"}
#: matching oracles whose planted 1% change is finer than the oracle's own tolerance, with why
UNPLANTABLE = {
    # the rate is -1 + 9.4e-10; 1% on any flow moves it by 1e-11, inside the test's rtol of 1e-9
    "numpy-financial/TestIrr::test_gh_15",
    # assert_allclose(actual, desired, 4): the third argument is rtol, so 400% passes (decimal=4 was meant)
    "numpy-financial/TestNper::test_broadcast",
}
#: pyxirr rate tests whose flows change sign twice: the test checks only that the rate it gets makes
#: the future value nil, which both roots do; the engine refuses and names both. Checked, not listed:
#: every root named must pass the test's own check
TWO_RATES = {"pyxirr/test_periodic::test_rate_non_zero_fv", "pyxirr/test_periodic::test_rate_pmt_at_beginning"}
#: oracles the reader declines, with the start of the reason it gives (none is a missing function)
READER_DECLINES = {
    # equality with a 28-digit Decimal pins Decimal's rounding (and, for rate, where Newton's method
    # stopped: the vendored answer 0.11069085371426893 is 3.5e-12 from the root, 0.1106908537107528...)
    **{oid: "the test asks for equality with a 28-digit Decimal" for oid in (
        "numpy-financial/TestIpmt::test_decimal", "numpy-financial/TestPV::test_pv_decimal",
        "numpy-financial/TestPmt::test_pmt_decimal_broadcast", "numpy-financial/TestPmt::test_pmt_decimal_simple",
        "numpy-financial/TestPpmt::test_decimal", "numpy-financial/TestRate::test_rate_decimal")},
    "numpy-financial/TestMirr::test_mirr_broadcast": "mirr over a table of streams and rates",
    "pyxirr/test_periodic::test_fv_vectorized_ndarray": "the reader cannot read `pyarray!",
}


def test_the_suites_are_vendored_and_in_the_manifest():
    files = O.load_manifest()
    for rel in [NUMPY, HELPERS] + [f"{PYXIRR}/{s}.rs" for s in ("test_periodic", "test_scheduled")]:
        assert rel in files
        O.verify(rel)
    samples = [k for k in files if k.startswith(f"{PYXIRR}/samples/")]
    assert len(samples) == 66
    for rel in samples:
        O.verify(rel)


def test_property_tests_and_shape_tests_are_not_oracles():
    assert not [oid for oid in ORACLES if oid in EXCLUDED]
    assert "numpy-financial/TestNpv::test_fuzz" not in ORACLES          # @given: no stored answer
    assert "numpy-financial/TestNpv::test_npv" in ORACLES
    assert "pyxirr/test_periodic::test_fv_macro_working" not in ORACLES  # tests the test macro, no library call
    # one oracle per parametrized case
    assert sum(oid.startswith("numpy-financial/TestIrr::test_basic_values[") for oid in ORACLES) == 6
    assert sum(oid.startswith("pyxirr/test_scheduled::test_xirr_samples[") for oid in ORACLES) == 60


def test_tolerances_flows_and_dates_are_read_from_the_vendored_files():
    assert _default_eps() == 1e-9                                       # helpers.rs, assert_almost_eq!
    assert _literal("&[87.17; 5]") == [87.17] * 5
    assert _literal("&[-1.44852555e+08, 15_000.0]") == [-144852555.0, 15000.0]
    assert _literal("f64::NAN") != _literal("f64::NAN")
    dates, amounts = _csv("tests/samples/1938.csv")                     # quoted dates, as csv.reader reads
    assert dates and len(dates) == len(amounts)


def test_a_refusal_never_satisfies_a_number_and_several_roots_are_a_refusal():
    assert not _near("irr", Refused("no rate"), 0.1, 1e-9).ok
    several = Refused("several", (-0.68, 0.12))
    assert not _near("irr", several, 0.12, 1e-9).ok
    assert not _refuses("irr", several).ok                                 # nor is it "no answer"
    assert _refuses("irr", Refused("no rate")).ok


def test_a_test_calling_a_function_the_engine_lacks_is_blocked_by_name():
    r = O.receipt(FAMILY, "pyxirr/test_periodic::test_cumprinc_works", commit="test")
    assert r.verdict == "blocked" and r.reason == "the engine has no cumprinc"
    r = O.receipt(FAMILY, "pyxirr/test_scheduled::test_xirr_day_count[0]", commit="test")
    assert r.verdict == "blocked" and "30/360 SIA" in r.reason


def test_the_xirr_samples_check_xnpv_only_where_a_rate_exists():
    # pyxirr: `if let Some(rate)`; a sample with no rate has one check, the refusal
    checks, _ = _run_pyxirr("pyxirr/test_scheduled::test_xirr_samples[case_30_29]", plant=False)
    assert [c.name for c in checks] == ["xirr"]
    checks, _ = _run_pyxirr("pyxirr/test_scheduled::test_xirr_samples[unordered]", plant=False)
    assert [c.name for c in checks] == ["xnpv at that xirr", "xirr"] and all(c.ok for c in checks)


def _library_rate_among_the_roots(r) -> bool:
    """Every failing check is a several-roots refusal that names the library's rate (or a call fed by one)."""
    for c in r.checks:
        if c["ok"] or "itself refused" in c["got"]:
            continue
        if "several rates fit" not in c["got"]:
            return False
        want = float(c["expected"].split(" ")[0])
        roots = [float(x) for x in c["got"].split(": ", 2)[2].split(", ")]
        if not any(abs(x - want) <= max(1e-6, 1e-2 * abs(want)) for x in roots):
            return False
    return True


@pytest.mark.parametrize("oid", ORACLES)
def test_every_oracle_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    if oid in READER_DECLINES:
        assert r.verdict == "blocked" and r.reason.startswith(READER_DECLINES[oid]), r.reason
    elif r.verdict == "blocked":
        assert r.reason.startswith(("the engine has no ", "the engine's xirr counts actual/365 only")), r.reason
    elif oid in TWO_RATES:
        assert r.verdict == "differs" and all(not c["ok"] for c in r.checks)
        _, eps = _future_value_eps()
        when = int("pmt_at_beginning" in oid)
        roots = [float(x) for x in r.checks[0]["got"].split(": ", 2)[2].split(", ")]
        assert len(roots) == 2
        for root in roots:     # PERIODS, PAYMENT, PV, FV of the test file; the macro's residual is -fv(...) + FV
            assert abs(110_000.0 - fv(root, 10.0, -50_000.0, 100_000.0, when)) < eps
    elif oid in SOLVER_MISSES:
        assert r.verdict == "differs" and r.checks[0]["expected"] == "no answer"
        root = float(r.checks[0]["got"])
        dates, amounts = _csv(f"tests/samples/{oid.split('case_')[1].rstrip(']').replace('_', '-')}.csv")
        assert xnpv(amounts, dates, root - 1e-6) * xnpv(amounts, dates, root + 1e-6) < 0
    elif oid in UNPLANTABLE:
        assert r.verdict == "blind" and all(c["ok"] for c in r.checks)
    elif r.verdict == "differs":
        assert _library_rate_among_the_roots(r), r.checks
    else:
        assert r.verdict == "matches", r.reason
        assert r.planted_caught


def test_the_numpy_reader_covers_every_numpy_financial_test_it_does_not_decline():
    for oid in _numpy_tests():
        if oid not in READER_DECLINES:
            assert O.receipt(FAMILY, oid, commit="test").verdict != "blocked", oid


def test_a_decimal_is_read_as_the_exact_number_it_writes():
    # Decimal("0.1") / Decimal("12") is 1/120 exactly, so ipmt's first-period interest is exact too
    checks, _ = _run_numpy("numpy-financial/TestIpmt::test_decimal_broadcasting", plant=False)
    assert [c.at for c in checks] == ["[0]", "[1]", "[2]", "[3]"] and all(c.ok for c in checks)
    assert checks[0].got == repr(float(-2000 / 120 * 1))


def test_an_expected_nan_is_agreed_only_by_a_refusal():
    # ipmt at period 0 has no answer: numpy NaN, the engine refuses; the other periods are numbers
    checks, _ = _run_numpy("numpy-financial/TestIpmt::test_broadcasting", plant=False)
    assert checks[0].got.startswith("refuses: period 0") and checks[0].ok
    assert all(c.ok and not c.got.startswith("refuses") for c in checks[1:])


def test_a_raises_check_over_an_array_needs_only_one_element_without_an_answer():
    checks, _ = _run_numpy("numpy-financial/TestRate::test_rate_maximum_iterations_exception_array", plant=False)
    assert len(checks) == 1 and checks[0].ok and checks[0].got.startswith("refuses: no rate")


def test_an_equality_of_two_calls_sees_the_plant_on_the_first():
    # each side is evaluated once: a second evaluation would be an unplanted second call
    planted, _ = _run_numpy("numpy-financial/TestFinancial::test_when", plant=True)
    assert not planted[0].ok and all(c.ok for c in planted[1:])
