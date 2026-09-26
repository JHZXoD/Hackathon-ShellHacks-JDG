"""Rough cost / impact estimate for a coordination opportunity (bonus deliverable).

Every number here is an assumption that the UI exposes and lets the user edit.
Two savings channels:
  1. Shared right-of-way - only when the projects are within 1.6 km: land the
     second project would not need to acquire (overlapping length x ROW width).
  2. Shared mobilization - crews, cranes, laydown yards, contractors - scaled
     by distance tier and by how close the build windows are.
Then: what that means per customer, since transmission capital goes into the
rate base that customer bills are set from.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd
from shapely.ops import transform

from gridlock.overlap import _to_m, geometry

M2_PER_ACRE = 4046.86
KM_PER_MI = 1.609344


@dataclass
class Assumptions:
    # typical transmission right-of-way widths (m) by voltage
    row_width_m_115: float = 30.0   # ~100 ft
    row_width_m_230: float = 38.0   # ~125 ft
    row_width_m_500: float = 61.0   # ~200 ft
    easement_cost_per_acre: float = 12_000.0  # rural SC/GA easement, incl. survey + legal
    # mobilization / site overhead as a share of a transmission project's cost
    mobilization_share: float = 0.08
    # rebuild cost per mile when a utility's cost is redacted (115-230 kV rebuild range)
    cost_per_mile_usd: float = 1_500_000.0
    fallback_project_cost_usd: float = 10_000_000.0  # point projects (substations) with no cost
    # electric customers, approx. public figures
    customers: dict = None

    def __post_init__(self):
        if self.customers is None:
            self.customers = {"Dominion Energy South Carolina": 800_000, "Georgia Power": 2_700_000}

    def row_width(self, kv: float | None) -> float:
        if kv and kv >= 500:
            return self.row_width_m_500
        if kv and kv >= 230:
            return self.row_width_m_230
        return self.row_width_m_115


# share of mobilization cost that can realistically be shared, by distance tier
TIER_SHARE = {"Touching / crossing": 0.5, "Under 1.6 km": 0.4, "Under 8 km": 0.25, "Under 40 km": 0.1}


def timing_factor(gap_days: float | None) -> float:
    """Crews can only be shared if the builds happen around the same time."""
    if gap_days is None or pd.isna(gap_days):
        return 0.3
    if gap_days <= 180:
        return 1.0
    if gap_days <= 365:
        return 0.7
    if gap_days <= 730:
        return 0.4
    return 0.1


def _project_cost(p: pd.Series, a: Assumptions, length_km: float) -> tuple[float, str]:
    if pd.notna(p.get("est_cost_usd")):
        return float(p.est_cost_usd), "utility filing"
    if length_km > 0:
        return length_km / KM_PER_MI * a.cost_per_mile_usd, f"estimated ({length_km / KM_PER_MI:.1f} mi x ${a.cost_per_mile_usd / 1e6:.1f}M/mi)"
    return a.fallback_project_cost_usd, "estimated (typical substation project)"


def estimate(pair: pd.Series, projects: pd.DataFrame, a: Assumptions | None = None) -> dict:
    a = a or Assumptions()
    pa = projects.set_index("project_id").loc[pair.project_id_a]
    pb = projects.set_index("project_id").loc[pair.project_id_b]
    ga, gb = transform(_to_m, geometry(pa)), transform(_to_m, geometry(pb))
    len_a, len_b = ga.length / 1000, gb.length / 1000

    # 1. shared right-of-way: length of the shorter line running within 1.6 km of the other
    shared_km = 0.0
    if pair.distance_km <= 1.6 and len_a > 0 and len_b > 0:
        short, other = (ga, gb) if len_a <= len_b else (gb, ga)
        shared_km = short.intersection(other.buffer(1600)).length / 1000
    kv = max(filter(pd.notna, [pa.voltage_kv, pb.voltage_kv]), default=None)
    width = a.row_width(kv)
    acres = shared_km * 1000 * width / M2_PER_ACRE
    row_savings = acres * a.easement_cost_per_acre

    # 2. shared mobilization on the smaller of the two projects
    cost_a, src_a = _project_cost(pa, a, len_a)
    cost_b, src_b = _project_cost(pb, a, len_b)
    t_factor = timing_factor(pair.time_gap_days)
    mob_savings = min(cost_a, cost_b) * a.mobilization_share * TIER_SHARE[pair.tier] * t_factor

    total = row_savings + mob_savings
    total_customers = a.customers[pa.utility] + a.customers[pb.utility]
    return {
        "shared_row_km": round(shared_km, 2),
        "row_width_m": width,
        "shared_acres": round(acres, 1),
        "row_savings_usd": round(row_savings),
        "cost_a_usd": round(cost_a), "cost_a_source": src_a,
        "cost_b_usd": round(cost_b), "cost_b_source": src_b,
        "timing_factor": t_factor,
        "tier_share": TIER_SHARE[pair.tier],
        "mobilization_savings_usd": round(mob_savings),
        "total_savings_usd": round(total),
        "savings_per_customer_usd": round(total / total_customers, 4),
        "rate_base_note": _rate_base_note(pa),
        "assumptions": asdict(a),
    }


def _rate_base_note(p: pd.Series) -> str | None:
    if pd.notna(p.get("est_cost_usd")) and pd.notna(p.get("rate_base_year")):
        return (f"{p.utility}'s filing applies ${p.est_cost_usd / 1e6:.1f}M for this project to its "
                f"{int(p.rate_base_year)} rate base, the capital that customer rates are set to recover.")
    return None
