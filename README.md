# one-engine

One engine for every financial model. Contracts and operations generate dated economic events. Each event is recorded as balanced two-ended postings in exact ledgers, in money or in physical units (tonnes, MWh, head), so value and matter are both conserved. Statements, metrics and returns are read off the ledgers. The structure follows Unified Financial Analysis (Brammertz et al., 2009) and ACTUS.

Built in `ctlxholdings/generic-framework` (branch `claude/project-thread-odlsa1`, through `1db9136`) and moved here with its history on 2026-10-22, so that every project consumes the same engine.

## Architecture

| Layer | Modules |
|---|---|
| **Inputs** | Time and units · Inputs and provenance · Market data · Entities |
| **Event generation** | Contracts · Portfolio and grain · Allocation · Conditions · Tax · Measurement · Holders · Loop solver |
| **Ledger** | Ledger kernel |
| **Analysis** | Cuts and lenses · Measures · Reports\* |
| **Control** | Run driver · Checks · Versions and governance\* |

\* Not yet built.

Domain content lives in **packs**: asset generators, contract types and rulebooks (PF, corporate, ACTUS, solar, process). A **case** is one model, declared in YAML.

## Invariants

Every posting has two ends, in one unit. Every balance equals opening plus postings, and no balance is written directly. Every run passes the integrity checks, and every check has been seen failing.

## Use it from another project

```
pip install "git+https://github.com/ctlxholdings/one-engine@<commit>"
```

```python
from one_engine.cases import run_case
result = run_case("path/to/case.yaml")
```

The package needs only PyYAML. `pip install ".[workbook]"` adds openpyxl for Excel oracles.

## Work on it

```
pip install -e ".[test]"
python -m pytest -q -p no:cacheprovider one_engine/tests        # full suite, about 20 minutes
python -m pytest -q one_engine/tests/test_chain.py              # the chain guards alone
python -m one_engine.oracles run                                # regenerate the scoreboard receipts
```

`one_engine/__init__.py` explains the architecture. `one_engine/cases/reference/` holds the vendored oracles, hash-pinned in `MANIFEST.json`. Never edit them; re-vendor with the new hash and origin. Design, decisions and the run log live in the project's `consolidation/` folder (`README.md`, `LOG.md`).
