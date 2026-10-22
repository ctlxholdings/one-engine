# one-engine

One engine for every financial model: an exact double-entry ledger, shared modules and packs, held to known answers. Built in `ctlxholdings/generic-framework` (branch `claude/project-thread-odlsa1`, through `1db9136`) and moved here with its history on 2026-10-22, so that every project consumes the same engine.

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
