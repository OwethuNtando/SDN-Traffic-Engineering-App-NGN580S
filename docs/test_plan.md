# Test plan and expected results (T1–T6, evidence F3/F4/F10)

Inputs (student 222313625): capacity C = 10 Mb/s per direction (DECLARED), IGP cost 10 per link,
delay 1 ms per link (ASSUMED), threshold T = 75 %, background on R1→R3 = 3.6 Mb/s, others 0.

Utilisation of a directed link: `U = (background + placed demand) / C × 100 %`.
Path MLU = max U over the path's links. Congested when U ≥ T.

## Hand calculation (should match the GUI and `tests/test_core.py`)

| Test | Demand D | Path | Link loads (Mb/s) | MLU | Decision |
|---|---|---|---|---|---|
| T1 | 3.625 | R1-R3 (cost 10) | L13: 3.6 + 3.625 = 7.225 | **72.25 %** < 75 | keep shortest path |
| T1 | 3.625 | R1-R2-R3 (cost 20) | L12: 3.625, L23: 3.625 | 36.25 % | not needed |
| T2 | 7.25 | R1-R3 | L13: 3.6 + 7.25 = 10.85 | **108.5 %** (overloaded) | threshold breached on L13 |
| T3 | 7.25 | R1-R2-R3 | L12: 7.25, L23: 7.25 | **72.5 %** | **re-route via R2**: MLU 108.5 → 72.5 % (−36 pp), +10 cost, +1 ms |

Bottleneck available capacity for T3: direct = 10 − 3.6 = 6.4 Mb/s < 7.25 (infeasible);
via R2 = 10 Mb/s (feasible).

## Procedure and evidence

| ID | Procedure | Pass criteria | Evidence |
|---|---|---|---|
| T1 Baseline | Start `--live`, Refresh, press *T1 baseline* | 3 nodes, 3 links, PCEP synchronized ×3, timestamps, source tags; L13 72.2 % | F0, F2, F4 screenshots; export CSV |
| T2 Congestion | *T2 congestion* or *Replay next profile step* to step 2. Optionally run the GA5 generator at 7.25 Mb/s for 36 s with the `cli` source | L13 red/OVERLOADED, warning in event log | F4 screenshot, CSV, router `show interfaces` |
| T3 Alternative | Inspect path table and decision box | R1-R2-R3 recommended, before/after MLU shown | F4 |
| T4 Provision | *Use TE proposal* → *Provision LSP* | progress reaches OPERATIONAL; Postman *LSP-DB R1* shows `operational: up`; R1 CLI shows tunnel up via 10.36.12.2 | F7: GUI + API inspector + Postman + CLI |
| T5 Teardown | (Re-sync first if any LSP is down) select an LSP that is up → *WITHDRAW / REMOVE SELECTED LSP* | WITHDRAWN only when absent from the LSP-DB; a down LSP is refused before sending; router refusal shown as FAILED with the PCEP reason | F8 |
| T6 Failure | (a) stop ODL / wrong port, Refresh and Provision; (b) Postman negative test; (c) invalid name `bad name!` | DISCONNECTED / FAILED, request not sent for invalid input, **no OPERATIONAL** | F2, F10 |
| X1 What-if | Fail L12 in Node/link tab | only R1-R3 candidate; split/warning shown | F6 |
| X2 History | Several refreshes across T1→T2 | graph shows L13 crossing the threshold line | F6 |
| X3 Export | *Export CSV* | file in `exports/` with provenance column | F6 |
| X4 Auto-refresh | tick auto-refresh 5 s | timestamp updates; age turns orange if refresh stalls | F6 |
| X5 Audit trail | open `logs/audit.jsonl` | every action/decision with timestamp | F6 |

Automated: `python -m pytest -q` (34 tests) covers the maths above, the LSP state machine,
validation, timeouts, and ODL HTTP/RPC error handling with a fake HTTP session.
