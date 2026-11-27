"""LSP workflow tests (T4-T6 logic) and OpenDaylight adapter behaviour with a fake HTTP
session. These test the application's handling only - they are NOT network evidence."""
import json
from pathlib import Path

import pytest
import requests

from teapp.config import load_config
from teapp.controller.base import ControllerRejected, ControllerUnreachable
from teapp.controller.odl import OdlAdapter
from teapp.controller.parsers import parse_pcep_topology
from teapp.controller.sim import SimAdapter
from teapp.lsp_manager import LspManager, resolve_lsp_path
from teapp.model import Topology

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture
def cfg():
    return load_config(str(ROOT / "config" / "config.example.json"))


@pytest.fixture
def env(cfg):
    topo = Topology.from_config(cfg)
    sim = SimAdapter({k: {"pcc_id": v["pcc_id"], "loopback": v["loopback"]} for k, v in cfg["nodes"].items()},
                     setup_delay_s=0, teardown_delay_s=0)
    mgr = LspManager(sim, topo, poll_s=0, timeout_s=1, sleep=lambda s: None)
    return topo, sim, mgr


def test_ero_uses_next_hop_ingress_addresses(env):
    _, _, mgr = env
    assert mgr.ero_for(["R1", "R2", "R3"]) == ["10.36.12.2", "10.36.23.2", "3.3.36.25"]
    assert mgr.ero_for(["R1", "R3"]) == ["10.36.13.2", "3.3.36.25"]


def test_T4_T5_provision_and_teardown_verified(env):
    topo, sim, mgr = env
    op = mgr.provision("SN3625-PCE-R1-R3-VIA-R2", ["R1", "R2", "R3"], sim.snapshot())
    states = [s.state for s in op.steps]
    assert states[:3] == ["INTENDED", "REQUESTED", "ACCEPTED"]
    assert op.state == "OPERATIONAL" and op.path_match is True
    assert op.observed_source == "RRO"
    td = mgr.teardown("SN3625-PCE-R1-R3-VIA-R2", "pcc://192.168.56.11")
    assert td.state == "WITHDRAWN"
    assert sim.snapshot().find_lsp("SN3625-PCE-R1-R3-VIA-R2") is None


def test_T6_controller_down_never_reports_success(env):
    _, sim, mgr = env
    snap = sim.snapshot()
    sim.fail_mode = "unreachable"
    op = mgr.provision("SN3625-PCE-R1-R3-DIRECT", ["R1", "R3"], snap)
    assert op.state == "FAILED" and "OPERATIONAL" not in [s.state for s in op.steps]


def test_T6_rpc_failure_reported(env):
    _, sim, mgr = env
    snap = sim.snapshot()
    sim.fail_mode = "reject"
    op = mgr.provision("SN3625-PCE-R1-R3-DIRECT", ["R1", "R3"], snap)
    assert op.state == "FAILED" and "rejected" in op.steps[-1].message


def test_accepted_but_never_up_times_out(env):
    _, sim, mgr = env
    sim.setup_delay = 1e9          # controller accepts but LSP never comes up
    op = mgr.provision("SN3625-PCE-R1-R3-DIRECT", ["R1", "R3"], sim.snapshot())
    assert op.state == "TIMEOUT"


def test_validation_blocks_bad_requests(env):
    topo, sim, mgr = env
    snap = sim.snapshot()
    assert mgr.validate("bad name!", ["R1", "R3"], snap)
    assert mgr.validate("ok", ["R1", "R1"], snap)
    topo.link("L13").up = False
    assert mgr.validate("ok", ["R1", "R3"], snap)
    op = mgr.provision("bad name!", ["R1", "R2", "R3"], snap)
    assert op.state == "FAILED" and len(sim.history) == 1   # only the snapshot GET, no add-lsp sent


def test_parse_fixture_and_resolve_path(cfg):
    doc = json.loads((FIX / "pcep_topology_shape.json").read_text())
    sessions = parse_pcep_topology(doc)
    assert {s.pcc for s in sessions} == {"pcc://192.168.56.11", "pcc://192.168.56.12", "pcc://192.168.56.13"}
    lsp = sessions[0].lsps[0]
    assert lsp.name == "SN3625-PCE-R1-R3-VIA-R2" and lsp.is_up
    rp = resolve_lsp_path(Topology.from_config(cfg), lsp)
    assert rp.nodes == ["R1", "R2", "R3"] and rp.link_ids == ["L12", "L23"] and rp.source == "RRO"


# ---------------------------------------------------------------- ODL adapter
class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class FakeSession(requests.Session):
    def __init__(self, responder):
        super().__init__()
        self.responder = responder
        self.calls = []

    def request(self, method, url, data=None, timeout=None, **kw):
        self.calls.append((method, url, data))
        return self.responder(method, url, data)


def odl(cfg, responder):
    return OdlAdapter(cfg["controller"], "u", "p", session=FakeSession(responder))


def test_odl_add_lsp_body_and_url(cfg):
    a = odl(cfg, lambda m, u, d: FakeResp(200, {"network-topology-pcep:output": {}}))
    a.add_lsp("pcc://192.168.56.11", "SN3625-PCE-R1-R3-VIA-R2", "1.1.36.25", "3.3.36.25", ["10.36.12.2", "10.36.23.2"])
    method, url, data = a.s.calls[-1]
    assert method == "POST" and url.endswith("/rests/operations/network-topology-pcep:add-lsp")
    body = json.loads(data)["input"]
    assert body["arguments"]["odl-pcep-ietf-stateful:lsp"] == {"delegate": True, "administrative": True}
    assert [s["ip-prefix"]["ip-prefix"] for s in body["arguments"]["ero"]["subobject"]] == \
        ["10.36.12.2/32", "10.36.23.2/32"]
    assert "pcep-topology" in body["network-topology-ref"]


def test_odl_rpc_failure_in_200_is_rejected(cfg):
    a = odl(cfg, lambda m, u, d: FakeResp(200, {"network-topology-pcep:output": {"failure": "unsent"}}))
    with pytest.raises(ControllerRejected):
        a.add_lsp("pcc://192.168.56.11", "x", "1.1.36.25", "3.3.36.25", ["10.36.13.2"])


def test_odl_http_error_and_unreachable(cfg):
    a = odl(cfg, lambda m, u, d: FakeResp(400, {"errors": {"error": [{"error-message": "bad input"}]}}))
    with pytest.raises(ControllerRejected, match="bad input"):
        a.remove_lsp("pcc://192.168.56.11", "x")

    def boom(m, u, d):
        raise requests.ConnectionError("refused")
    b = odl(cfg, boom)
    with pytest.raises(ControllerUnreachable):
        b.snapshot()
    assert b.history[-1].error


def test_odl_missing_topology_is_reported_not_crashing(cfg):
    a = odl(cfg, lambda m, u, d: FakeResp(409, {"errors": {"error": [{"error-tag": "data-missing"}]}}))
    snap = a.snapshot()
    assert snap.reachable and snap.pccs == [] and snap.errors


def test_odl_reads_r1_bgp_peer_from_rib(cfg):
    def resp(m, u, d):
        if "bgp-rib" in u:
            assert "peer=bgp%3A%2F%2F192.168.56.11" in u
            return FakeResp(200, {"bgp-rib:peer": [{"peer-id": "bgp://192.168.56.11",
                                                    "supported-tables": [{"afi": "ipv4-unicast"}, {"afi": "linkstate"}]}]})
        return FakeResp(200, {"network-topology:topology": [{"topology-id": "pcep-topology"}]})
    snap = odl(cfg, resp).snapshot()
    assert snap.bgp_peer_state.startswith("ESTABLISHED")


def test_odl_reads_bgp_session_state_openconfig(cfg):
    def resp(m, u, d):
        if "openconfig-network-instance" in u:
            assert "neighbor=192.168.56.11/state" in u and "BGP,bgp-3625" in u
            return FakeResp(200, {"openconfig-network-instance:state": {
                "session-state": "ESTABLISHED", "neighbor-address": "192.168.56.11",
                "bgp-openconfig-extensions:supported-capabilities": ["ASN32", "MPBGP"]}})
        return FakeResp(200, {"network-topology:topology": [{"topology-id": "pcep-topology"}]})
    snap = odl(cfg, resp).snapshot()
    assert snap.bgp_peer_state.startswith("ESTABLISHED (peer 192.168.56.11")


def test_odl_bgp_idle_is_not_established(cfg):
    def resp(m, u, d):
        if "openconfig-network-instance" in u:
            return FakeResp(200, {"state": {"session-state": "openconfig-bgp-types:ACTIVE"}})
        return FakeResp(200, {"network-topology:topology": [{"topology-id": "pcep-topology"}]})
    snap = odl(cfg, resp).snapshot()
    assert snap.bgp_peer_state.startswith("ACTIVE")


def test_pcep_error_19_1_is_explained():
    from teapp.lsp_manager import explain_pcep_error
    txt = 'remove-lsp RPC returned failure=\'failed\' [{"error-object": {"type": 19, "processing-rule": false, "ignore": false, "value": 1}}]'
    assert "NOT DELEGATED" in explain_pcep_error(txt)
    assert "try again" in explain_pcep_error("add-lsp RPC returned failure='no-ack' []")


def test_teardown_rejected_by_router_is_failed_not_withdrawn(env):
    _, sim, mgr = env
    mgr.provision("SN3625-PCE-R1-R3-VIA-R2", ["R1", "R2", "R3"], sim.snapshot())
    sim.fail_mode = "reject"
    td = mgr.teardown("SN3625-PCE-R1-R3-VIA-R2", "pcc://192.168.56.11")
    assert td.state == "FAILED" and "WITHDRAWN" not in [s.state for s in td.steps]


def test_withdraw_blocked_when_tunnel_down(env):
    _, sim, mgr = env
    mgr.provision("SN3625-PCE-R1-R3-VIA-R2", ["R1", "R2", "R3"], sim.snapshot())
    sim._lsps[("pcc://192.168.56.11", "SN3625-PCE-R1-R3-VIA-R2")]["state"] = "down"
    td = mgr.teardown("SN3625-PCE-R1-R3-VIA-R2", "pcc://192.168.56.11", snapshot=sim.snapshot())
    assert td.state == "FAILED" and "not up" in td.steps[-1].message
    assert not any(r.url.endswith("remove-lsp") for r in sim.history)


def test_resync_removes_abandoned_down_tunnel(env):
    _, sim, mgr = env
    mgr.provision("SN3625-PCE-R1-R3-VIA-R2", ["R1", "R2", "R3"], sim.snapshot())
    mgr.provision("SN3625-PCE-R1-R3-DIRECT", ["R1", "R3"], sim.snapshot())
    sim._lsps[("pcc://192.168.56.11", "SN3625-PCE-R1-R3-DIRECT")]["state"] = "down"
    op = mgr.resync("pcc://192.168.56.11")
    assert op.state == "SYNCED" and "SN3625-PCE-R1-R3-DIRECT" in op.steps[-1].message
    snap = sim.snapshot()
    assert snap.find_lsp("SN3625-PCE-R1-R3-DIRECT") is None and snap.find_lsp("SN3625-PCE-R1-R3-VIA-R2").is_up


def test_odl_trigger_sync_body(cfg):
    seen = {}
    def resp(m, u, d):
        seen["u"], seen["d"] = u, d
        return FakeResp(200, {"output": {}})
    odl(cfg, resp).trigger_sync("pcc://192.168.56.11")
    assert seen["u"].endswith("network-topology-pcep:trigger-sync") and "pcc://192.168.56.11" in str(seen["d"])
