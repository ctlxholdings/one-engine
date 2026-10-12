"""Command line for oracle receipts.

    python3 -m one_engine.oracles verify
        check every file in cases/reference/MANIFEST.json against its hash
    python3 -m one_engine.oracles show <family> <oracle id>
        run one oracle and print its receipt, every comparison row by row
    python3 -m one_engine.oracles run [<family> ...] --out <folder> [--inventory <csv>]
        receipt every oracle of the families (all by default), write
        <folder>/receipts/<family>/<id>.json and <folder>/SCOREBOARD.md
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from one_engine import oracles as O


def _family(name: str) -> O.Family:
    for f in O.families():
        if f.name == name:
            return f
    sys.exit(f"unknown family {name!r}; known: {[f.name for f in O.families()]}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m one_engine.oracles")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    s = sub.add_parser("show")
    s.add_argument("family")
    s.add_argument("oracle")
    r = sub.add_parser("run")
    r.add_argument("families", nargs="*")
    r.add_argument("--out", required=True, type=pathlib.Path)
    r.add_argument("--inventory", type=pathlib.Path)
    a = p.parse_args(argv)

    if a.cmd == "verify":
        files = O.load_manifest()
        for rel in sorted(files):
            O.verify(rel)
        print(f"{len(files)} oracle files match their recorded hashes")
        return 0
    if a.cmd == "show":
        rec = O.receipt(_family(a.family), a.oracle)
        print(f"oracle   {rec.family} {rec.oracle}\nsource   cases/reference/{rec.source}\n"
              f"sha256   {rec.sha256}\nengine   {rec.engine_commit}\nrule     {rec.tolerance}")
        for c in rec.checks:
            print(f"{'ok ' if c['ok'] else 'BAD'}  {c['at']:<22} {c['name']:<22} "
                  f"oracle {c['expected']:<24} engine {c['got']}")
        print(f"planted  {rec.planted}: {'caught' if rec.planted_caught else 'NOT caught' if rec.planted_caught is False else 'not run'}"
              + (f" ({rec.planted_failures} checks failed)" if rec.planted_caught else ""))
        print(f"verdict  {rec.verdict}" + (f": {rec.reason}" if rec.reason else ""))
        return 0 if rec.verdict in ("matches", "blocked") else 1
    fams = [_family(n) for n in a.families] if a.families else O.families()
    out = {f.name: O.run_family(f, a.out) for f in fams}
    (a.out / "SCOREBOARD.md").write_text(O.scoreboard(out, fams, a.inventory))
    for name, rs in out.items():
        print(name, {v: sum(r.verdict == v for r in rs) for v in O.VERDICTS})
    return 0


if __name__ == "__main__":
    sys.exit(main())
