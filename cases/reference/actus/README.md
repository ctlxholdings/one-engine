# ACTUS reference test cases

The 19 files `actus-tests-<type>.json` are copied verbatim from the ACTUS Financial Research Foundation's public test repository `actusfrf/actus-tests`, folder `tests/`. The four loan files were fetched on 2026-10-09; all 19 were re-copied on 2026-10-12 from golds (`one-place`, `cases/external/actus`), and the four loan files were byte-identical to the earlier fetch. Their SHA-256 hashes are recorded in `../MANIFEST.json`.

- **Licence:** CC BY-SA 4.0. Attribution: ACTUS Financial Research Foundation. Any adaptation of these files must be shared under the same licence. The files are unmodified here.
- **Contents:** 277 contracts of 19 types: ANN 31, LAM 31, PAM 25, OPTNS 23, NAM 22, LAX 18, CEC 15, CLM 15, CEG 14, FUTUR 14, SWPPV 14, FXOUT 12, SWAPS 11, STK 10, UMP 9, CAPFL 4, COM 4, CSH 4, and one ANN contract in `ad0` that adds an analysis-date event. Each has its contract terms, the market data and events it observes, and the expected event stream: event dates, types, payoffs and the state after each event.
- **Use:**
  - `one_engine/oracles/actus.py` runs every contract and writes a receipt per contract: `python3 -m one_engine.oracles show actus pam/pam01`.
  - `one_engine/tests/test_oracles.py` asserts each contract's verdict (matches, or blocked with the missing contract type named).
  - `one_engine/tests/test_case_actus.py` pins the rules the loan files forced.
