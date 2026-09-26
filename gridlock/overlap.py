"""Cross-utility overlap detection and ranking (Sperry GridLock spec).

Geographic overlap (primary): closest-point distance between the two projects'
geometries - a line between its two endpoint substations, or a single point -
measured in an equal-area projection. Under 40 km (25 mi) is an overlap.
Center-to-center haversine distance (the starter-file method) is reported too.

Timeline overlap (secondary): gap in days between planned in-service dates.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import transform

from gridlock.geocode import haversine_mi

ROOT = Path(__file__).resolve().parent.parent
KM_PER_MI = 1.609344
OVERLAP_KM = 40.0

# (max km, label, what the two utilities can share) - checked in order
TIERS = [
    (0.1, "Touching / crossing", "Must coordinate: outage timing, crossing structures"),
    (1.6, "Under 1.6 km", "Share the land itself: right-of-way, access roads, permits"),
    (8.0, "Under 8 km", "Share site logistics: laydown yards, deliveries"),
    (40.0, "Under 40 km", "Share crews and equipment"),
]
TIER_POINTS = {"Touching / crossing": 40, "Under 1.6 km": 30, "Under 8 km": 15, "Under 40 km": 0}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.9, "low": 0.7}
TIMELINE_WINDOW_DAYS = 3 * 365  # beyond this the build windows don't meaningfully overlap

_to_m = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform  # CONUS Albers, meters


def geometry(row) -> LineString | Point | None:
    pts = [(row[f"lon_{t}"], row[f"lat_{t}"]) for t in "ab" if pd.notna(row.get(f"lat_{t}"))]
    if not pts:
        return None
    if len(pts) == 2 and pts[0] != pts[1]:
        return LineString(pts)
    return Point(pts[0])


def tier_for(km: float) -> tuple[str, str] | None:
    for max_km, label, share in TIERS:
        if km <= max_km:
            return label, share
    return None


def score(km: float, gap_days: float | None, conf_a: str, conf_b: str) -> float:
    """0-100. Distance dominates; timeline is a strong secondary signal;
    low-confidence locations are discounted."""
    tier = tier_for(km)
    geo = 45 * (1 - km / OVERLAP_KM) + TIER_POINTS[tier[0]] * 0.5 if tier else 0
    time = 0 if gap_days is None else 35 * max(0.0, 1 - gap_days / TIMELINE_WINDOW_DAYS)
    weight = min(CONFIDENCE_WEIGHT.get(conf_a, 0.5), CONFIDENCE_WEIGHT.get(conf_b, 0.5))
    return round((geo + time) * weight, 1)


@dataclass
class Located:
    row: pd.Series
    geom_m: object


def find_overlaps(projects: pd.DataFrame, utility_a: str, utility_b: str, max_km: float = OVERLAP_KM) -> pd.DataFrame:
    located = {}
    for _, r in projects.iterrows():
        g = geometry(r)
        if g is not None:
            located[r.project_id] = Located(r, transform(_to_m, g))
    side_a = [l for l in located.values() if l.row.utility == utility_a]
    side_b = [l for l in located.values() if l.row.utility == utility_b]

    rows = []
    for a in side_a:
        for b in side_b:
            km = a.geom_m.distance(b.geom_m) / 1000
            if km > max_km:
                continue
            ra, rb = a.row, b.row
            da, db = pd.to_datetime(ra.in_service_date), pd.to_datetime(rb.in_service_date)
            gap = abs((da - db).days) if pd.notna(da) and pd.notna(db) else None
            label, share = tier_for(km)
            rows.append({
                "project_id_a": ra.project_id, "project_name_a": ra.project_name, "utility_a": ra.utility,
                "project_id_b": rb.project_id, "project_name_b": rb.project_name, "utility_b": rb.utility,
                "distance_km": round(km, 2),
                "distance_mi": round(km / KM_PER_MI, 2),
                "center_distance_mi": round(haversine_mi(ra.lat_center, ra.lon_center, rb.lat_center, rb.lon_center), 2),
                "tier": label, "can_share": share,
                "time_gap_days": gap,
                "in_service_a": da.date() if pd.notna(da) else None,
                "in_service_b": db.date() if pd.notna(db) else None,
                "confidence_a": ra.confidence, "confidence_b": rb.confidence,
                "score": score(km, gap, ra.confidence, rb.confidence),
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values(["score", "distance_km"], ascending=[False, True]).reset_index(drop=True)
    out.insert(0, "rank", out.index + 1)
    out.insert(1, "overlap_id", [f"OVL_{i}" for i in out["rank"]])
    return out


def load_projects() -> pd.DataFrame:
    return pd.read_csv(ROOT / "data" / "processed" / "projects.csv", parse_dates=["in_service_date"])


if __name__ == "__main__":
    projects = load_projects()
    ov = find_overlaps(projects, "Dominion Energy South Carolina", "Georgia Power")
    ov.to_csv(ROOT / "data" / "processed" / "overlaps.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 45)
    print(f"{len(ov)} overlapping pairs")
    print(ov[["rank", "project_id_a", "project_name_a", "project_id_b", "project_name_b",
              "distance_mi", "center_distance_mi", "tier", "time_gap_days", "confidence_a", "confidence_b", "score"]]
          .head(40).to_string(index=False))
