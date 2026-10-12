"""The oracle receipt harness, and the ACTUS corpus run through it.

The harness is itself a check, so it gets the same treatment as every other
check: each way it can go wrong is planted and must be caught.

* a changed or unlisted oracle file refuses before anything runs;
* a comparison that cannot fail (the planted error goes unseen, or there is
  no comparison at all) is ``blind``, never ``matches``;
* a disagreement is ``differs`` and names the first difference;
* a missing engine piece is ``blocked`` and names the piece.

Then the ACTUS family: every one of the 277 published contracts gets a
receipt, every receipt's verdict is the one recorded here, and none is blind.
"""
import json
import shutil

import pytest

from one_engine import oracles as O
from one_engine.oracles.actus import ActusFamily


# ── the manifest ────────────────────────────────────────────────────────────

def test_every_vendored_oracle_file_is_in_the_manifest_and_unchanged():
    files = O.load_manifest()
    on_disk = {f"actus/{p.name}" for p in (O.REFERENCE / "actus").glob("*.json")}
    assert on_disk <= set(files)
    for rel in files:
        O.verify(rel)


def test_a_changed_oracle_refuses(tmp_path):
    root = tmp_path / "reference"
    (root / "actus").mkdir(parents=True)
    rel = "actus/actus-tests-pam.json"
    shutil.copy(O.REFERENCE / rel, root / rel)
    O.verify(rel, root=root)                                         # unchanged: passes
    data = json.loads((root / rel).read_text())
    data["pam01"]["results"][1]["payoff"] = "25.48"                  # someone "fixes" an answer
    (root / rel).write_text(json.dumps(data))
    with pytest.raises(O.OracleChanged, match="differs from the manifest"):
        O.verify(rel, root=root)
    (root / rel).unlink()
    with pytest.raises(O.OracleChanged, match="missing"):
        O.verify(rel, root=root)
    with pytest.raises(O.OracleChanged, match="not in the manifest"):
        O.verify("actus/a-file-nobody-recorded.json", root=root)


# ── verdicts, on families built to fail in one way each ─────────────────────

class _Fake(O.Family):
    """Borrows a real manifest file so the hash step passes; ``behaviour`` picks the failure."""

    name, planted, tolerance = "fake", "the fake's planted error", "exact"

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def oracles(self):
        return ["x"]

    def source(self, oid):
        return "actus/actus-tests-pam.json"

    def run(self, oid, plant=False):
        b = self.behaviour
        if b == "blocked":
            raise O.Blocked("the engine has no widget")
        if b == "empty":
            return [], {}
        if b == "differs":
            return [O.Check("v", "t0", "1", "1", True), O.Check("v", "t1", "2", "3", False)], {}
        # "honest" sees the plant; "blind" passes whatever is planted
        ok = not (plant and b == "honest")
        return [O.Check("v", "t0", "1", "1" if ok else "2", ok)], {"a": 1}



@pytest.mark.parametrize("behaviour, verdict, reason", [
    ("honest", "matches", ""),
    ("blind", "blind", "planted error went unseen"),
    ("empty", "blind", "no check"),
    ("differs", "differs", "1 of 2 checks disagree; first: v at t1"),
    ("blocked", "blocked", "the engine has no widget"),
])
def test_each_verdict_is_given_only_when_earned(behaviour, verdict, reason):
    r = O.receipt(_Fake(behaviour), "x", commit="test")
    assert r.verdict == verdict
    assert reason in r.reason
    assert r.sha256 == O.load_manifest()["actus/actus-tests-pam.json"]["sha256"]
    if verdict == "matches":
        assert r.planted_caught is True and r.planted_failures == 1 and r.inputs == {"a": 1}
    if verdict == "differs":
        assert r.first_difference == {"name": "v", "at": "t1", "expected": "2", "got": "3", "ok": False}


# ── the ACTUS corpus ────────────────────────────────────────────────────────

FAMILY = ActusFamily()
ORACLES = FAMILY.oracles()
BUILT = {"pam", "lam", "nam", "ann", "ad0", "csh", "clm", "ump", "lax"}


def test_the_whole_corpus_is_receipted():
    assert len(ORACLES) == 277
    stems = {o.split("/")[0] for o in ORACLES}
    assert len(stems) == 19


@pytest.mark.parametrize("oid", ORACLES)
def test_every_actus_oracle_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    assert r.verdict != "blind", r.reason
    if oid.split("/")[0] in BUILT:
        assert r.verdict == "matches", r.reason
        assert r.planted_caught and r.n_checks > 2
    else:
        assert r.verdict == "blocked", (r.verdict, r.reason)
        assert "not built" in r.reason


def test_the_scoreboard_counts_and_names(tmp_path):
    only = ["pam/pam01", "swaps/swaps01"]
    rs = O.run_family(FAMILY, tmp_path, only=only)
    assert sorted(p.name for p in (tmp_path / "receipts" / "actus").iterdir()) == \
        ["pam__pam01.json", "swaps__swaps01.json"]
    saved = json.loads((tmp_path / "receipts" / "actus" / "pam__pam01.json").read_text())
    assert saved["verdict"] == "matches" and saved["checks"][0]["name"] == "events"
    board = O.scoreboard({"actus": rs}, [FAMILY])
    assert "| actus | 2 | 1 | 0 | 1 | 0 |" in board
    assert "ACTUS contract type SWAPS is not built" in board and "swaps/swaps01" in board
