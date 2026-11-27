"""Student-number parameterisation (NGN580S GA5 section 5, T3 section 7).

Rule (GA5 brief, section 5): use the LAST FOUR DIGITS of the student number.

    Student number : 222313625
    Last four      : D = 3625  ->  A = 36 (first pair), B = 25 (second pair)

Derived identifiers (GA5, retained in T3):
    (a) Loopback0            Rn Lo0 = n.n.A.B/32            -> 1.1.36.25 / 2.2.36.25 / 3.3.36.25
                              link subnets 10.A.<xy>.0/24   -> 10.36.12/13/23.0/24, tunnel-te3625
    (b) Router-ID / BGP       router-id = Lo0; BGP AS = 4200000000 + D -> 4200003625 (iBGP R1<->ODL, GA5)
    (c) LSP naming (GA5)     SN<D>-PCE-<HEAD>-<TAIL>-<PATH>  e.g. SN3625-PCE-R1-R3-VIA-R2
                              (GA5 delegated tunnel: tunnel-te3625 / SN3625-R1-R3)
    (d) Packet generator      offered rate  = D kb/s          -> 3.625 Mb/s
                              test duration = A seconds       -> 36 s

Student-specific TE inputs (T3 section 7):
    congestion threshold      = 50 + B  %                    -> 75 %
    background load on R1-R3  = A / 10  Mb/s                 -> 3.6 Mb/s
    congestion (step) demand  = 2 x baseline demand          -> 7.25 Mb/s

All values are produced here, in one place, so the derivation can be shown and
defended. If your GA5 build used different values, override them in
config/config.json; the GUI shows both the derived and the effective values.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class StudentParams:
    student_number: str
    last4: int
    pair_a: int
    pair_b: int
    loopbacks: dict
    bgp_asn: int
    lsp_prefix: str
    baseline_demand_mbps: float
    congestion_demand_mbps: float
    test_duration_s: int
    threshold_pct: float
    background_r1r3_mbps: float

    def lsp_name(self, head: str, tail: str, path_tag: str) -> str:
        """Apply the student LSP naming rule, e.g. SN3625-PCE-R1-R3-VIA-R2."""
        tag = "".join(ch for ch in path_tag.upper() if ch.isalnum() or ch == "-").strip("-")
        return f"{self.lsp_prefix}-{head.upper()}-{tail.upper()}-{tag}"

    def as_dict(self) -> dict:
        return asdict(self)

    def derivation_text(self) -> str:
        d, a, b = self.last4, self.pair_a, self.pair_b
        return "\n".join([
            f"Student number           : {self.student_number}",
            f"Last four digits (D)     : {d}   A = {a:02d}, B = {b:02d}",
            f"Loopback0 (n.n.A.B)      : " + ", ".join(f"{k}={v}" for k, v in self.loopbacks.items()),
            f"BGP AS (4200000000 + D)  : {self.bgp_asn}",
            f"LSP naming rule (GA5)    : {self.lsp_prefix}-<HEAD>-<TAIL>-<PATH>  e.g. {self.lsp_name('R1', 'R3', 'VIA-R2')}",
            f"Baseline demand (D kb/s) : {self.baseline_demand_mbps:.3f} Mb/s",
            f"Congestion demand (2x)   : {self.congestion_demand_mbps:.3f} Mb/s",
            f"Test duration (A s)      : {self.test_duration_s} s",
            f"Congestion threshold     : 50 + B = {self.threshold_pct:.0f} %",
            f"Background on R1-R3      : A/10 = {self.background_r1r3_mbps:.1f} Mb/s",
        ])


def derive(student_number: str) -> StudentParams:
    """Derive every student-specific parameter from the student number."""
    digits = "".join(ch for ch in str(student_number) if ch.isdigit())
    if len(digits) < 4:
        raise ValueError("Student number must contain at least four digits")
    last4 = int(digits[-4:])
    a, b = int(digits[-4:-2]), int(digits[-2:])
    loopbacks = {f"R{n}": f"{n}.{n}.{a}.{b}" for n in (1, 2, 3)}
    baseline = last4 / 1000.0  # D kb/s expressed in Mb/s
    return StudentParams(
        student_number=digits,
        last4=last4,
        pair_a=a,
        pair_b=b,
        loopbacks=loopbacks,
        bgp_asn=4_200_000_000 + last4,
        lsp_prefix=f"SN{last4:04d}-PCE",
        baseline_demand_mbps=baseline,
        congestion_demand_mbps=round(2 * baseline, 3),
        test_duration_s=a if a > 0 else 30,
        threshold_pct=float(min(95, 50 + b)),
        background_r1r3_mbps=a / 10.0,
    )
