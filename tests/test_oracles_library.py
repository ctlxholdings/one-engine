"""The numpy-financial and pyxirr test suites run through the oracle harness, and the reader that reads them.

The reader is the only thing authored here (there is no translation), so the
ways it could pass a case dishonestly are each pinned:

* a refusal never satisfies a numeric answer, and several roots are a
  refusal even when the library's pick is among them;
* a test that calls a function the engine lacks is blocked, naming it;
* tolerances, flows and dates come from the vendored files (the default
  ``assert_almost_eq!`` tolerance from ``helpers.rs``, quoted CSV dates as
  ``csv.reader`` reads them).

Then every oracle gets its recorded verdict. The differences are of two
kinds, both checked rather than listed: the engine refuses a stream with
several rates where the library picks one (every such refusal must name the
library's rate among its roots), and two pyxirr samples where the library
finds no rate and the engine finds one (its XNPV must change sign across it).
"""
import pytest

from one_engine import oracles as O
from one_engine.measures import xnpv
from one_engine.oracles.library import (EXCLUDED, HELPERS, NUMPY, PYXIRR, LibraryFamily, Refused, _csv,
                                        _default_eps, _literal, _near, _numpy_tests, _refuses, _run_pyxirr)

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
    r = O.receipt(FAMILY, "numpy-financial/TestPmt::test_pmt_simple", commit="test")
    assert r.verdict == "blocked" and r.reason == "the engine has no pmt"
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
    if r.verdict == "blocked":
        assert r.reason.startswith(("the engine has no ", "the engine's xirr counts actual/365 only")), r.reason
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


def test_the_numpy_reader_covers_every_npv_and_irr_test():
    ran = [oid for oid in _numpy_tests() if "TestIrr" in oid or "TestNpv" in oid]
    for oid in ran:
        assert O.receipt(FAMILY, oid, commit="test").verdict != "blocked", oid
