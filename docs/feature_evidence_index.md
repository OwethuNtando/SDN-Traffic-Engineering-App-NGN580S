# Feature–evidence index (T3 §9 / §10)

Fill the *Evidence files* column with your numbered captures from `evidence/` and the
*Report §* column with your report section numbers.

| ID | Requirement | GUI control | Source module(s) | Test | Evidence files | Report § |
|---|---|---|---|---|---|---|
| F0 | GA5 topology reused, student parameters, connectivity | Topology view, *Student parameters* tab, PCEP labels | `student.py`, `config.py`, `service._refresh_controller` | T1 | | |
| F1 | GUI screenshot + architecture diagram | whole window | `docs/architecture.md` | – | | |
| F2 | Launch, data retrieval, refresh status, errors | *Refresh now*, auto-refresh, top bar, event log | `controller/odl.py`, `service.refresh` | T1, T6 | | |
| F3 | TE problem, assumptions, algorithm, inputs | TE tab inputs, policy text | `te_engine.py`, `docs/test_plan.md` | – | | |
| F4 | Baseline / congestion / path comparison with calculations | link table, path table, decision box | `te_engine.compute` | T1–T3 | | |
| F5 | Operator workflow, before/after views | T1/T2 buttons, decision box, topology colours | `gui/app.py` | T1–T3 | | |
| F6 | Extra features, separately tested | history, export, what-if, auto-refresh, inventory, API inspector, audit | `audit.py`, `gui/*` | X1–X5 | | |
| F7 | LSP provision: request + operational verification | *Provision LSP*, progress box, API inspector | `lsp_manager.provision`, `odl.add_lsp` | T4 | | |
| F8 | LSP teardown: request + absence verification | *WITHDRAW / REMOVE SELECTED LSP* | `lsp_manager.teardown`, `odl.remove_lsp` | T5 | | |
| F9 | LSP path in GUI vs operational path | inventory selection → purple overlay (from RRO/ERO) | `lsp_manager.resolve_lsp_path`, `topology_view` | T4 | | |
| F10 | Outcomes, limitations, reproducibility | – | README §7, tests | all | | |
| F11 | Live demo + code walkthrough | – | GUI: `gui/`; TE: `te_engine.py`; controller: `controller/odl.py` | – | | |
