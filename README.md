# NGN580S SDN Traffic Engineering & Network Management Application

Student **222313625** · NGN580S T3 Final Summative · Python 3 + Tkinter

The app manages my GA5 network (GNS3 project *Tunzi GA5*: R1, R2, R3 IOS XRv 6.1.2 full mesh,
OpenDaylight 0.20.2 at 192.168.56.110, BGP R1–ODL, PCEP R1/R2/R3–ODL, MPLS-TE).
It shows the live topology and controller state, works out link utilisation for an R1→R3 demand,
compares the direct path R1-R3 with R1-R2-R3, adds a PCE-initiated LSP through OpenDaylight and can withdraw (remove) it again.
An LSP is only reported as working after it is seen **operational = up** in the controller's PCEP LSP database.

## 1. Install (once)

Python 3.9+ with Tkinter (Anaconda or python.org Python already include Tkinter).

```
cd Tunzi_222313625_code
pip install -r requirements.txt
```

## 2. Run (no .bat file)

```
python te_app.py              # LIVE: GA5 lab (OpenDaylight + GNS3)
python te_app.py --offline    # OFFLINE SIMULATION (red banner) - core TE demo only
python te_app.py --test       # automated tests (34 tests)
```

Or open `te_app.py` in Spyder / IDLE / VS Code and press **Run**.

**Windows CMD:** if CMD says *"Python was not found"*, Anaconda is not on the PATH. Use the full path:

```
cd "<unzip folder>\Tunzi_222313625-Code\Tunzi_222313625_code"
"C:\ProgramData\anaconda3\python.exe" te_app.py
```

(or run `conda init cmd.exe` once in Anaconda Prompt, then `python te_app.py` works in any new CMD).
When the window opens, click **ODL login** and type the OpenDaylight username and password.
They stay in memory only; nothing secret is stored in `config/` or in the code.

Before starting LIVE: GNS3 project *Tunzi GA5* open with R1–R3 started, and OpenDaylight running
in the Ubuntu VM (`cd ~/karaf-0.20.2/bin && ./start`).

## 3. The window

| Area | What it shows / does |
|---|---|
| Status strip | ODL state, **BGP R1-ODL: ESTABLISHED** (green), PCEP x/3 synced, GNS3 routers started, last refresh time and age, Refresh, Auto-refresh, ODL login |
| Topology (left) | R1/R2/R3 and links L12/L13/L23 from GNS3, utilisation per direction (green/amber/red), PCEP lines, solid green **BGP ESTABLISHED** line R1–ODL, blue TE proposal, purple verified LSP. Click a router or link for details and the link-failure what-if |
| Tab 1 Traffic engineering | Demand and threshold inputs, **T1 baseline** / **T2 congestion** / **Calculate**, link table with data source, path comparison, decision in plain words with before/after MLU |
| Tab 2 LSPs (add / remove) | Path, auto LSP name, strict ERO, **ADD LSP**, progress (requested → accepted → reported → OPERATIONAL / WITHDRAWN), LSP list from the controller with a *delegated* column (click to draw its path), **WITHDRAW / REMOVE SELECTED LSP** (only for LSPs that are up), **RE-SYNC LSPs** |
| Tab 3 History & tools | Utilisation history graph, **Export CSV**, load data source / replay, API log (every RESTCONF request and response), student parameters |
| Event log (bottom) | Every action and result, also saved in `logs/audit.jsonl` |

## 4. Demo workflow (T1–T6)

1. Start the app, **ODL login**, **Refresh**: ODL CONNECTED, BGP ESTABLISHED, PCEP 3/3, GNS3 3/3.
2. **T1 baseline (3.625)**: L13 R1→R3 = 72.2 % (< 75 %) → keep R1-R3.
3. **T2 congestion (7.25)**: L13 = 108.5 % OVERLOADED → R1-R2-R3 recommended (MLU 72.5 %).
4. Tab 2: **Use TE proposal** → name `SN3625-PCE-R1-R3-VIA-R2`, ERO 10.36.12.2 → 10.36.23.2 → 3.3.36.25 → **ADD LSP**.
   Wait for **OPERATIONAL**. Click the LSP: purple path R1-R2-R3 drawn from the controller's ERO/RRO.
5. Check independently: Postman (`postman/`) LSP-DB, and on R1 `show mpls traffic-eng tunnels brief`.
6. T5: the new LSP stays selected after ADD → **WITHDRAW / REMOVE SELECTED LSP** → remove-lsp is sent; WITHDRAWN is shown only when the LSP is gone from the LSP database. If R1 refuses (PCEP error 19/1, LSP not delegated) the app says so in plain words.
7. T6: stop ODL or enter a bad name (`bad name!`) → DISCONNECTED / request not sent; no false success.

## 4b. Re-sync abandoned tunnels

An add-lsp that R1 answers with `no-ack` can leave an *abandoned* auto-tunnel on R1 (state down,
e.g. tunnel-te2500 / te2503). A tunnel that is down cannot be withdrawn, so the app blocks WITHDRAW for
any LSP that is not up and tells the operator to re-sync first.

* **RE-SYNC LSPs** (tab 2) sends `POST /rests/operations/network-topology-pcep:trigger-sync` for R1 and
  then compares the LSP list before and after: it lists the LSPs that were removed, the ones whose state
  changed, and the ones that are still not up. It never assumes success.
* If R1 does not support trigger-sync (IOS XRv 6.1.2 may not), re-sync from the router side: on R1 run
  `show mpls traffic-eng tunnels brief` to find the down auto-tunnels (marked `>`), then reset the PCEP
  session so R1 re-sends its full LSP state (`clear mpls traffic-eng pce`; check with `?` on your XR
  version). Press **Refresh** in the app afterwards.

## 5. How BGP state is read

`GET /rests/data/openconfig-network-instance:network-instances/network-instance=global-bgp/openconfig-network-instance:protocols/protocol=openconfig-policy-types%3ABGP,bgp-3625/bgp-openconfig-extensions:bgp/neighbors/neighbor=192.168.56.11/state`
→ field `session-state` (the same value Karaf shows with
`bgp:operational-state --rib bgp-3625 --neighbor 192.168.56.11`). Code: `teapp/controller/odl.py::_bgp_state`.

## 6. Student-number parameters (`teapp/student.py`)

222313625 → last four digits D = 3625, A = 36, B = 25.

| Item | Rule | Value |
|---|---|---|
| Loopbacks | n.n.A.B | 1.1.36.25, 2.2.36.25, 3.3.36.25 |
| BGP AS / RIB | 4200000000 + D / bgp-D | 4200003625 / bgp-3625 |
| LSP name | SN<D>-PCE-<HEAD>-<TAIL>-<PATH> | SN3625-PCE-R1-R3-VIA-R2 |
| Baseline demand (T1) | D kbit/s | 3.625 Mb/s |
| Congestion demand (T2) | 2 × baseline | 7.25 Mb/s |
| Threshold | 50 + B % | 75 % |
| Background on R1→R3 | A/10 Mb/s | 3.6 Mb/s |
| Test duration | A s | 36 s |

## 7. Assumptions and limitations

* Link capacity 10 Mb/s is **declared** (XRv in GNS3 is CPU-bound; BGP-LS link data not used). Delay 1 ms/link is **assumed**.
* Every value carries a source tag: CONTROLLER, DECLARED, MANUAL, REPLAY, MEASURED (CLI), COMPUTED, SIMULATED.
* "After" utilisation assumes the demand follows the LSP (needs autoroute/static route into the tunnel on R1).
* What-if link failure changes only the calculation, never the routers.
* The offline simulator is labelled everywhere and does not count as network evidence.
* R1 sometimes answers add-lsp with `no-ack` (first attempt on 14 Nov 17:17); the app reports FAILED and a repeat request worked (17:21, OPERATIONAL).

## 8. Files

```
te_app.py            launcher - run this
teapp/
  gui/               app.py (window), topology_view.py (canvas), history_view.py (graph)
  controller/        odl.py (RESTCONF adapter), parsers.py, sim.py (offline), base.py
  service.py         application logic (refresh, recompute, add/withdraw LSP, export)
  te_engine.py       TE model: utilisation, MLU, path comparison, decision
  lsp_manager.py     add/withdraw LSP workflow + verification in the LSP database
  gns3.py            live topology from the GNS3 REST API
  telemetry.py       load sources   student.py  student parameters   audit.py  log/history/CSV
config/              config.json (lab, no secrets), config.example.json (template)
data/                sample_load_profile.csv (replay profile)
postman/             collection for independent checks
docs/                architecture, test plan, feature–evidence index, router prerequisites
tests/               pytest suite
evidence/            screenshots and logs
```

## 9. Acknowledgements

Python standard library (tkinter, threading, json, csv), `requests`, `pytest`, optional `netmiko`;
OpenDaylight BGPCEP RESTCONF API; GNS3 REST API. AI assistance (Anthropic Claude) was used during
development and is declared in the report.
