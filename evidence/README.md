# Evidence index (live GA5 lab, 2026-11-14, app run with `python te_app.py`)

Note: the app adds and withdraws (removes) LSPs. Screenshots marked "addonly" were taken while the
remove button was temporarily hidden; the add/visualise behaviour is identical.

| File | ID | What it shows |
|---|---|---|
| F0_F2_T1_live_BGP_established_baseline.jpg | F0, F2, T1 | 17:15 LIVE: ODL CONNECTED, **BGP R1-ODL ESTABLISHED**, PCEP 3/3 synced, GNS3 3/3 started; T1 3.625 Mb/s -> L13 72.2 % (< 75 %), keep R1-R3 |
| F4_T2_T3_live_congestion_reroute.jpg | F4, F5, T2, T3 | 17:15:36 T2 7.25 Mb/s -> L13 108.5 % OVERLOADED; R1-R2-R3 RECOMMENDED, MLU 72.5 % |
| F9_live_LSP_overlay_VIA_R2_up.jpg | F9 | LSP SN3625-PCE-R1-R3-VIA-R2 selected: operational=up, purple path R1-R2-R3 drawn from controller ERO 10.36.12.2, 10.36.23.2, 3.3.36.25 |
| F10_T6_live_remove_refused_by_router_no_false_success.jpg | F8 (attempt), F10, T6 | 17:16:27 remove-lsp for SN3625-PCE-R1-R3-VIA-R2 refused by R1 (PCEP error 19/1 = LSP not delegated to the PCE) -> FAILED, no false WITHDRAWN |
| F7_T4_live_add_noack_no_false_success.jpg | F7, T6 | 17:17:34 first add-lsp attempt: R1 gave `no-ack` -> FAILED, no false success |
| F7_F9_T4_live_ADD_LSP_OPERATIONAL_VIA_R2_T4.jpg | F7, F9, T4 | 17:21:46 ADD LSP SN3625-PCE-R1-R3-VIA-R2-T4: REQUESTED -> ACCEPTED -> REPORTED -> **OPERATIONAL**, LSP count 3 -> 4, purple path R1-R2-R3 from controller ERO |
| F6_F7_live_history_export_API_log_addlsp_204.jpg | F6, F7 | History graph (L13 crossing 75 %), Export CSV, API log: POST add-lsp -> 204 (693 ms) then LSP-DB / BGP reads -> 200 |
| F0_F2_T1_live_baseline_addonly.jpg | F0, F2, T1 | 17:47 add-only app: BGP R1-ODL ESTABLISHED, PCEP 3/3, GNS3 3/3; T1 L13 72.2 % |
| F4_T2_T3_live_congestion_addonly.jpg | F4, T2, T3 | 17:48:17 add-only app: L13 108.5 % OVERLOADED, R1-R2-R3 RECOMMENDED 72.5 % |
| F7_F9_live_LSP_list_T4_overlay_addonly.jpg | F7, F9 | 17:49 add-only app: 5 LSPs up; SN3625-PCE-R1-R3-VIA-R2-T4 drawn on L12+L23 from controller ERO |
| F4c_iperf3_H3_receiver_1250K_36s.jpg | F4 (MEASURED) | iperf3 server H3: 36.05 s, 5.36 MB, 1.25 Mbit/s, jitter 1.321 ms, 0/9000 lost (0 %) |
| F4d_iperf3_H1_client_1250K_36s.jpg | F4 (MEASURED) | iperf3 client H1 (-u -b 1250K -l 625 -t 36 -p 3625 --get-server-output): same result, "iperf Done" |
| F4e_iperf3_H3_receiver_run2_1250K.jpg | F4 (MEASURED) | iperf3 repeat run on H3 (seen 18:12): 36.02 s, 5.36 MB, 1.25 Mbit/s, jitter 0.572 ms, 0/9000 lost |
| F7b_R1_CLI_mpls_te_tunnels_brief.jpg | F7 (router) | R1 18:17:58 SAST: 7 TE heads to 3.3.36.25, 5 up (PCE auto-tunnels te2501/2502/2504/2505 + GA5 tunnel-te3625), 2 down (te2500, te2503 = the no-ack attempts) |
| F7_F9_T4_live_ADD_LSP_F7_OPERATIONAL_addonly.jpg | F7, F9, T4 | 18:59:34 add-only app: ADD LSP SN3625-PCE-R1-R3-VIA-R2-F7 REQUESTED -> ACCEPTED -> REPORTED -> OPERATIONAL first try, LSPs 5 -> 6, purple path R1-R2-R3 |
| F7c_live_API_log_addlsp_F7_204_addonly.jpg | F7 | API log: POST add-lsp -> 204 (702 ms) then LSP-DB and BGP reads -> 200; request body shown |
| F6_te_export_20261114_172241.csv | F6 | Exported history + TE result with provenance column |
| audit_live_2026-11-14_T1-T6.jsonl | F2, F10 | Full audit trail of this run (BGP ESTABLISHED line, decisions, add/remove results with every step) |
| F2a_*, F4b_*, F9a_*, F10a_*, audit_live.jsonl, audit_live_2026-11-14.jsonl | – | Earlier runs with the old GUI (13 Nov and 14 Nov afternoon), kept for the record |

Router-side check: F7b (R1 `show mpls traffic-eng tunnels brief`).
