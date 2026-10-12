"""Oracle receipts: run a stored answer through the engine and leave a record anyone can check.

Why this exists
---------------
Ali holds thousands of stored answers ("oracles") in many forms: standard
test vectors, published deal figures, hand-derived golds, workbooks. Running
one is only worth something if a reader can see, without trusting the
runner, *what* was compared, *against which exact file*, and *that the
comparison could have failed*. Each run therefore produces a **receipt**:

* the oracle file and its SHA-256, checked against ``cases/reference/
  MANIFEST.json`` before anything runs (an oracle that changed since it was
  vendored refuses; we never edit an oracle);
* the inputs handed to the engine, as they were handed (the translation is
  the only thing we author, so it is shown in full);
* every comparison, row by row: what the oracle says, what the engine says,
  whether they agree at the oracle family's stated tolerance;
* a **planted error**: the same oracle run again with one input perturbed
  on purpose. If the comparison still passes, the check is blind and the
  receipt says so (verdict ``blind``); a blind check never counts;
* the engine's git commit, and the one command that reproduces the receipt.

Verdicts
--------
``matches``  every check agrees, and the planted error was caught.
``differs``  at least one check disagrees. The oracle wins: the engine or
             the translation is wrong, never the stored number.
``blocked``  the engine lacks a piece the oracle needs; the reason names it.
``blind``    the run produced no check, or the planted error went unseen.

How a family plugs in
---------------------
A *family* is one storage form of oracle (ACTUS JSON, gold YAML, ...). It
subclasses :class:`Family` and provides ``oracles()``, ``source(oid)`` and
``run(oid, plant)``; see :mod:`one_engine.oracles.actus`. The harness does the
rest: hashing, the planted run, the verdict, the files.

This module never mutates engine state and never writes inside the
repository; receipts and the scoreboard go to a folder the caller names.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import pathlib
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Where vendored oracle files live, and the manifest that pins their hashes.
REFERENCE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "reference"
MANIFEST = REFERENCE / "MANIFEST.json"
REPO = pathlib.Path(__file__).resolve().parents[2]

VERDICTS = ("matches", "differs", "blocked", "blind")


class OracleChanged(RuntimeError):
    """An oracle file is missing from the manifest or no longer has its recorded hash."""


class Blocked(Exception):
    """The engine lacks a piece this oracle needs. The message names the piece."""


@dataclass(frozen=True)
class Check:
    """One comparison: a named quantity at a place (date, row, period), oracle value vs engine value.

    Values are kept as strings so a receipt prints them exactly as compared.
    """

    name: str
    at: str
    expected: str
    got: str
    ok: bool


@dataclass
class Receipt:
    """The record of one oracle run. See the module docstring for each field's purpose."""

    family: str
    oracle: str
    source: str
    sha256: str
    command: str
    engine_commit: str
    verdict: str
    reason: str = ""
    tolerance: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    n_checks: int = 0
    n_failed: int = 0
    first_difference: Optional[Dict[str, Any]] = None
    planted: str = ""
    planted_caught: Optional[bool] = None
    planted_failures: int = 0
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, default=str)


# ── the manifest ────────────────────────────────────────────────────────────

def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(manifest: pathlib.Path = MANIFEST) -> Dict[str, Dict[str, str]]:
    return json.loads(manifest.read_text())["files"]


def verify(relpath: str, root: pathlib.Path = REFERENCE, manifest: pathlib.Path = MANIFEST) -> str:
    """Return the file's SHA-256 if it matches the manifest; refuse otherwise.

    The manifest is written once, when a file is vendored, from the copy at
    its origin. A mismatch means the stored answer moved; the run stops
    rather than compare against an answer nobody vouched for.
    """
    files = load_manifest(manifest)
    if relpath not in files:
        raise OracleChanged(f"{relpath}: not in the manifest, so nobody recorded where it came from")
    path = root / relpath
    if not path.exists():
        raise OracleChanged(f"{relpath}: listed in the manifest but missing")
    got = sha256(path)
    if got != files[relpath]["sha256"]:
        raise OracleChanged(f"{relpath}: hash {got[:12]}… differs from the manifest {files[relpath]['sha256'][:12]}…")
    return got


def engine_commit() -> str:
    """The git commit the engine ran at, with ``+dirty`` when the working tree has changes."""
    try:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "one_engine"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# ── families ────────────────────────────────────────────────────────────────

class Family:
    """One storage form of oracle. Subclasses fill in the four members below."""

    #: short name, used in commands and folder names
    name: str = ""
    #: one sentence: what the planted error is, so a reader knows what "caught" means
    planted: str = ""
    #: the comparison rule, in words, as the oracle states it
    tolerance: str = ""

    def oracles(self) -> List[str]:
        """Every oracle id this family holds, in a stable order."""
        raise NotImplementedError

    def source(self, oid: str) -> str:
        """The oracle's file, relative to ``cases/reference`` (the manifest key)."""
        raise NotImplementedError

    def run(self, oid: str, plant: bool = False) -> Tuple[List[Check], Dict[str, Any]]:
        """Run one oracle; return its checks and the inputs given to the engine.

        With ``plant=True`` one engine input is perturbed as ``planted``
        describes; the oracle side is never touched. Raise :class:`Blocked`
        when the engine lacks a piece the oracle needs.
        """
        raise NotImplementedError

    def inventory_oid(self, row: Mapping[str, str]) -> Optional[str]:
        """If an inventory row (``oracle_inventory.csv``) is one of this family's oracles, its id."""
        return None


def receipt(family: Family, oid: str, commit: Optional[str] = None) -> Receipt:
    """Run one oracle normally and planted, and return its receipt."""
    src = family.source(oid)
    digest = verify(src)
    base = Receipt(family=family.name, oracle=oid, source=src, sha256=digest,
                   command=f"python3 -m one_engine.oracles show {family.name} {oid}",
                   engine_commit=commit if commit is not None else engine_commit(),
                   verdict="blind", tolerance=family.tolerance, planted=family.planted)
    try:
        checks, inputs = family.run(oid)
    except Blocked as e:
        base.verdict, base.reason = "blocked", str(e)
        return base
    except Exception as e:                # the engine failed on an oracle it claims to build
        base.verdict, base.reason = "differs", f"the engine raised {type(e).__name__}: {e}"
        return base
    base.inputs = inputs
    base.checks = [asdict(c) for c in checks]
    base.n_checks = len(checks)
    failed = [c for c in checks if not c.ok]
    base.n_failed = len(failed)
    if not checks:
        base.reason = "the run produced no check"
        return base
    if failed:
        base.verdict = "differs"
        base.first_difference = asdict(failed[0])
        base.reason = f"{len(failed)} of {len(checks)} checks disagree; first: {failed[0].name} at {failed[0].at}"
    try:
        planted_checks, _ = family.run(oid, plant=True)
    except Blocked as e:
        if not failed:
            base.reason = f"the planted run could not be made: {e}"
        return base
    except Exception:
        if failed:                        # the verdict already stands; the plant only cannot be shown
            return base
        raise
    # the plant is seen when it breaks a figure that agreed; on a differing case this shows the
    # figures that do agree can still fail, so the differences are not the only thing tested.
    # A check is matched by name and place; a figure that agreed everywhere by name alone, since a
    # series names where it fails ("element 3"), not where it agreed ("all 4 elements")
    agreed = {(c.name, c.at) for c in checks if c.ok}
    whole = {c.name for c in checks if c.ok} - {c.name for c in failed}
    base.planted_failures = sum(not c.ok and ((c.name, c.at) in agreed or c.name in whole)
                                for c in planted_checks)
    base.planted_caught = base.planted_failures > 0
    if failed:
        return base
    if base.planted_caught:
        base.verdict = "matches"
    else:
        base.reason = "the planted error went unseen, so these checks cannot fail"
    return base


# ── scoreboard ──────────────────────────────────────────────────────────────

def run_family(family: Family, out: Optional[pathlib.Path] = None,
               only: Optional[Sequence[str]] = None) -> List[Receipt]:
    """Receipt every oracle of a family; with ``out``, write ``receipts/<family>/<id>.json`` there."""
    commit = engine_commit()
    rows = [receipt(family, oid, commit) for oid in (only or family.oracles())]
    if out is not None:
        folder = out / "receipts" / family.name
        folder.mkdir(parents=True, exist_ok=True)
        for r in rows:
            (folder / (r.oracle.replace("/", "__") + ".json")).write_text(r.to_json())
    return rows


def scoreboard(receipts: Mapping[str, List[Receipt]], families: Iterable[Family],
               inventory: Optional[pathlib.Path] = None) -> str:
    """The scoreboard as markdown: per family, verdict counts and every non-match by name;
    then, when the inventory CSV is given, how many inventoried oracle rows have a receipt."""
    when = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commits = sorted({r.engine_commit for rs in receipts.values() for r in rs})
    lines = ["# Oracle scoreboard", "",
             f"Generated {when} from engine commit {', '.join(commits) or 'n/a'}. "
             "Written by `python3 -m one_engine.oracles run`; do not edit by hand.", "",
             "Verdicts: **matches** (every check agrees and a planted error was caught), "
             "**differs** (the oracle wins), **blocked** (the engine lacks a named piece), "
             "**blind** (the check could not fail; never counts).", "",
             "| Family | Oracles | Matches | Differs | Blocked | Blind |", "|---|---|---|---|---|---|"]
    for name, rs in receipts.items():
        n = {v: sum(r.verdict == v for r in rs) for v in VERDICTS}
        lines.append(f"| {name} | {len(rs)} | {n['matches']} | {n['differs']} | {n['blocked']} | {n['blind']} |")
    for name, rs in receipts.items():
        lines += ["", f"## {name}", ""]
        for v in ("differs", "blind", "blocked"):
            bad = [r for r in rs if r.verdict == v]
            if not bad:
                continue
            lines.append(f"**{v}** ({len(bad)}):")
            groups: Dict[str, List[str]] = {}
            for r in bad:
                groups.setdefault(r.reason, []).append(r.oracle)
            for reason, ids in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                lines.append(f"- {reason}: {', '.join(ids)}")
            lines.append("")
        ok = [r.oracle for r in rs if r.verdict == "matches"]
        lines.append(f"**matches** ({len(ok)}): {', '.join(ok) if ok else 'none'}")
    if inventory is not None and inventory.exists():
        lines += ["", "## Inventory coverage", "",
                  "Rows of `oracle_inventory.csv` in tiers A and B, and how many now carry a receipt. "
                  "A row counts as covered when a family claims it and its receipt says matches.", "",
                  "| System | Tier A+B rows | With a receipt | Matches |", "|---|---|---|---|"]
        fams = list(families)
        verdict = {(r.family, r.oracle): r.verdict for rs in receipts.values() for r in rs}
        per: Dict[str, List[int]] = {}
        for row in csv.DictReader(inventory.open()):
            if not row["tier"].startswith(("A", "B")):
                continue
            c = per.setdefault(row["system"], [0, 0, 0])
            c[0] += 1
            for f in fams:
                oid = f.inventory_oid(row)
                if oid is not None and (f.name, oid) in verdict:
                    c[1] += 1
                    c[2] += verdict[(f.name, oid)] == "matches"
                    break
        for system, (n, have, ok) in per.items():
            lines.append(f"| {system} | {n} | {have} | {ok} |")
        tot = [sum(v[i] for v in per.values()) for i in range(3)]
        lines.append(f"| **Total** | **{tot[0]}** | **{tot[1]}** | **{tot[2]}** |")
    return "\n".join(lines) + "\n"


def families() -> List[Family]:
    """Every family the harness knows. Imported lazily so the harness has no engine imports itself."""
    from one_engine.oracles.actus import ActusFamily
    from one_engine.oracles.golds import GoldFamily
    from one_engine.oracles.esco import EscoFamily
    from one_engine.oracles.published import PublishedFamily
    return [ActusFamily(), GoldFamily(), PublishedFamily(), EscoFamily()]
