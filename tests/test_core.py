"""Unit tests: student derivation, TE engine (T1-T3 calculations), config validation,
telemetry parsing. Run:  python -m pytest -q"""
import copy
import json
from pathlib import Path

import pytest

from teapp.config import ConfigError, apply_student_defaults, load_config, validate
from teapp.model import Topology
from teapp.student import derive
from teapp.te_engine import compute, enumerate_paths
from teapp import telemetry as tel

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(str(ROOT / "config" / "config.example.json"))


def test_student_derivation():
    sp = derive("222313625")
    assert sp.last4 == 3625 and sp.pair_a == 36 and sp.pair_b == 25
    assert sp.loopbacks == {"R1": "1.1.36.25", "R2": "2.2.36.25", "R3": "3.3.36.25"}
    assert sp.bgp_asn == 4200003625
    assert sp.lsp_name("R1", "R3", "via-R2") == "SN3625-PCE-R1-R3-VIA-R2"
    assert sp.baseline_demand_mbps == 3.625 and sp.congestion_demand_mbps == 7.25
    assert sp.threshold_pct == 75 and sp.test_duration_s == 36
    assert sp.background_r1r3_mbps == 3.6


def test_student_number_too_short():
    with pytest.raises(ValueError):
        derive("12")


def test_paths_full_mesh(cfg):
    topo = Topology.from_config(cfg)
    assert enumerate_paths(topo, "R1", "R3") == [["R1", "R3"], ["R1", "R2", "R3"]]


def test_T1_baseline_no_reroute(cfg):
    topo = Topology.from_config(cfg)
    r = compute(topo, "R1", "R3", 3.625, 75, "min_mlu")
    assert r.baseline.name == "R1-R3" and r.recommended is r.baseline
    assert r.baseline.mlu == pytest.approx((3.6 + 3.625) / 10 * 100)   # 72.25 %
    assert not r.baseline.congested


def test_T2_T3_congestion_triggers_alternative(cfg):
    topo = Topology.from_config(cfg)
    r = compute(topo, "R1", "R3", 7.25, 75, "min_mlu")
    assert r.baseline.congested and r.baseline.mlu == pytest.approx(108.5)
    assert r.recommended.name == "R1-R2-R3"
    assert r.recommended.mlu == pytest.approx(72.5)
    assert r.mlu_before == pytest.approx(108.5) and r.mlu_after == pytest.approx(72.5)
    assert r.recommended.tag == "VIA-R2"


def test_igp_policy_ignores_load(cfg):
    topo = Topology.from_config(cfg)
    r = compute(topo, "R1", "R3", 7.25, 75, "igp")
    assert r.recommended.name == "R1-R3"


def test_split_when_no_single_path_fits(cfg):
    topo = Topology.from_config(cfg)
    r = compute(topo, "R1", "R3", 12.0, 75, "min_mlu")
    assert r.split is not None
    assert r.split.mlu < min(c.mlu for c in r.candidates)


def test_link_failure_what_if(cfg):
    topo = Topology.from_config(cfg)
    topo.link("L12").up = False
    r = compute(topo, "R1", "R3", 7.25, 75, "min_mlu")
    assert [c.name for c in r.candidates] == ["R1-R3"]
    assert any("No single path" in w for w in r.warnings)


def test_input_validation(cfg):
    topo = Topology.from_config(cfg)
    with pytest.raises(ValueError):
        compute(topo, "R1", "R3", -1, 75)
    with pytest.raises(ValueError):
        compute(topo, "R1", "R3", 1, 150)


def test_config_validation_rejects_bad_ip():
    raw = json.loads((ROOT / "config" / "config.example.json").read_text())
    bad = copy.deepcopy(raw)
    bad["links"][0]["a_ip"] = "10.36.300.1"
    with pytest.raises(ConfigError):
        validate(apply_student_defaults(bad))


def test_xr_rate_parser():
    out = "  30 second input rate 1000 bits/sec, 1 packets/sec\n  30 second output rate 7250000 bits/sec, 600 packets/sec"
    assert tel.parse_output_rate_bps(out) == 7250000


def test_replay_profile_loads():
    steps = tel.load_replay(ROOT / "data" / "sample_load_profile.csv")
    assert steps[0].demand_mbps == 3.625 and steps[2].demand_mbps == 7.25
    assert steps[-1].elapsed_s == 36
