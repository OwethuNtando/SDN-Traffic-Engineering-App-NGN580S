"""GNS3 adapter: parsing of /v2 projects/nodes/links with a fake HTTP session (shape test only)."""
import json

import requests

from teapp.gns3 import Gns3Client, expand_if


class R:
    def __init__(self, data, status=200):
        self.status_code, self._d = status, data
        self.text = json.dumps(data)

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


class S(requests.Session):
    def __init__(self, routes):
        super().__init__()
        self.routes = routes

    def get(self, url, timeout=None, **kw):
        for k, v in self.routes.items():
            if url.endswith(k):
                return R(v)
        raise requests.ConnectionError(url)


def node(i, name, status="started", t="qemu"):
    return {"node_id": i, "name": name, "status": status, "x": 0, "y": 0, "node_type": t}


def lk(a, la, b, lb):
    return {"nodes": [{"node_id": a, "label": {"text": la}}, {"node_id": b, "label": {"text": lb}}]}


def test_expand_if():
    assert expand_if("Gi0/0/0/1") == "GigabitEthernet0/0/0/1"
    assert expand_if("e0") == "Ethernet0"


def test_snapshot_filters_router_links():
    routes = {
        "/v2/projects": [{"project_id": "p", "name": "Tunzi GA5", "status": "opened"}],
        "/v2/projects/p/nodes": [node("1", "R1"), node("2", "R2"), node("3", "R3", "stopped"),
                                 node("4", "Switch1", t="ethernet_switch"), node("5", "H1", t="vpcs")],
        "/v2/projects/p/links": [lk("1", "Gi0/0/0/0", "2", "Gi0/0/0/0"), lk("1", "Gi0/0/0/1", "3", "Gi0/0/0/1"),
                                 lk("1", "Gi0/0/0/2", "4", "e0"), lk("1", "Gi0/0/0/3", "5", "eth0")],
    }
    c = Gns3Client({"project": "Tunzi GA5"}, session=S(routes))
    snap = c.snapshot()
    assert snap.ok and snap.project == "Tunzi GA5"
    assert [(l.a, l.b, l.a_if) for l in snap.links] == [("R1", "R2", "GigabitEthernet0/0/0/0"),
                                                       ("R1", "R3", "GigabitEthernet0/0/0/1")]
    assert snap.nodes["R3"].status == "stopped"


def test_unreachable_is_reported():
    c = Gns3Client({}, session=S({}))
    assert "unreachable" in c.snapshot().error
