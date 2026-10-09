# ACTUS reference test cases

Files `actus-tests-{pam,lam,nam,ann}.json` are copied verbatim from the ACTUS Financial Research Foundation's public test repository `actusfrf/actus-tests`, folder `tests/`, fetched 2026-10-09.

- **Licence:** CC BY-SA 4.0. Attribution: ACTUS Financial Research Foundation. Any adaptation of these files must be shared under the same licence. The files are unmodified here.
- **Contents:** 109 loan contracts (PAM 25, LAM 31, NAM 22, ANN 31). Each has its contract terms, the market data it observes, and the expected event stream: event dates, types, payoffs and the state after each event.
- **Use:** referee for the golds loan case. `one_engine/tests/test_case_actus.py` runs every contract on the engine and compares it event by event.
