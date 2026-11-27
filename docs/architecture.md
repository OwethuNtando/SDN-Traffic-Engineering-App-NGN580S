# Architecture (evidence F1)

## Frontend (operator GUI)

```mermaid
flowchart LR
  subgraph GUI["Tkinter GUI - teapp/gui"]
    TB["Status strip: LIVE/OFFLINE, ODL, BGP R1-ODL ESTABLISHED,<br/>PCEP x/3, GNS3 x/3, last refresh, Refresh, Auto, ODL login"]
    TV["Topology view: R1/R2/R3, links L12/L13/L23 (from GNS3),<br/>utilisation colours, PCEP + BGP lines, TE proposal,<br/>verified LSP overlay; click = details / what-if"]
    subgraph Tabs
      TE["1 Traffic engineering: demand, threshold, T1/T2,<br/>link table, path comparison, decision"]
      LSP["2 LSPs (add / remove): path, name, ERO, ADD LSP,<br/>progress, LSP list from controller, WITHDRAW LSP"]
      HI["3 History & tools: graph, CSV export,<br/>replay, API log, student parameters"]
    end
    EV["Event log (mirrors logs/audit.jsonl)"]
  end
```

## Backend

```mermaid
flowchart TB
  GUI["GUI (main thread)"] -- "run_bg() worker threads / queue" --> SVC["service.AppService<br/>state, refresh, audit"]
  SVC --> TE["te_engine<br/>paths, MLU, decision, split"]
  SVC --> LM["lsp_manager<br/>state machine + verification"]
  SVC --> TEL["telemetry<br/>declared / manual / replay / cli"]
  SVC --> AUD["audit<br/>events, history, CSV"]
  LM --> AD{{"ControllerAdapter"}}
  SVC --> AD
  AD --> ODL["controller.odl<br/>RESTCONF RFC 8040"]
  AD -.offline.-> SIM["controller.sim<br/>LABELLED SIMULATOR"]
  ODL -- "HTTP :8181 /rests" --> C["OpenDaylight 0.20.2<br/>bgpcep PCE + BGP-LS"]
  C -- "PCEP (PCInitiate / PCRpt)" --> R1 & R2 & R3
  C -- "BGP(-LS)" --- R1
  TEL -- "SSH show interfaces (netmiko)" --> R1 & R2 & R3
  R1 --- R2 --- R3 --- R1
```

## LSP provisioning and verification sequence

```mermaid
sequenceDiagram
  participant Op as Operator
  participant G as GUI
  participant M as LspManager
  participant O as OpenDaylight
  participant R as R1 (PCC)
  Op->>G: choose path + name, Provision
  G->>M: provision(name, [R1,R2,R3])
  M->>M: validate (name, links up, PCC session, no duplicate)
  M->>O: POST add-lsp (strict ERO)
  O->>R: PCInitiate
  O-->>M: 200 output{} -> ACCEPTED (not success)
  R-->>O: PCRpt (LSP state, RRO)
  loop every verify_poll_s until verify_timeout_s
    M->>O: GET pcep-topology LSP-DB
  end
  M-->>G: REPORTED -> OPERATIONAL (operational=up, path from RRO compared to intent)
  G-->>Op: overlay drawn from controller RRO; operator cross-checks with Postman + CLI
```

## Modules and responsibilities

| Module | Responsibility | Rubric link |
|---|---|---|
| `gui/app.py` | Windows, panels, input validation, feedback; no network calls on the UI thread | Core GUI |
| `gui/topology_view.py` | Topology, status, utilisation colours, verified LSP overlay | Core GUI, LSP visualisation |
| `service.py` | Single application state, refresh cycle, audit | Architecture |
| `te_engine.py` | Congestion model and path comparison | TE model |
| `lsp_manager.py` | Intended/requested/accepted/reported/operational; withdraw_requested/withdrawn | LSP add and withdraw (remove) |
| `controller/odl.py` | Real RESTCONF adapter, error classification | Architecture (adapter), Reliability |
| `controller/sim.py` | Clearly labelled offline mode | Reliability (honest state) |
| `telemetry.py` | Load sources with provenance | Status/provenance |
