"""Attach coordinates to each project by matching its endpoint names to
OpenStreetMap substations / plants (Sperry guide, Part 1) and grading
each match's confidence (Part 2).

Confidence levels:
  high   - exact normalized name match, operator agrees with the utility
  medium - exact or near-exact name match, operator unknown / other
  low    - fuzzy match only
  manual - from data/overrides.csv (hand-verified or Sperry starter file)
"""
from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from itertools import product
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OSM_CACHE = ROOT / "data" / "cache" / "osm_substations.json"
OVERRIDES = ROOT / "data" / "overrides.csv"

UTILITY_OPERATORS = {
    "Dominion Energy South Carolina": re.compile(r"dominion|sce&?g|south carolina (electric|gas)", re.I),
    "Georgia Power": re.compile(r"georgia (power|transmission)|meag|oglethorpe|southern", re.I),
}
MAX_ENDPOINT_GAP_MI = 80  # the two ends of one project shouldn't be further apart than this
FALLBACK_MAX_GAP_MI = 40  # stricter limit when one end is only a place-name guess

_PREFIX = re.compile(r"^((SAV|GTC|MEAG|DU|CC|GRID)\s*[:\-]\s*)+", re.I)
_KV = re.compile(r"\d{2,3}(\.\d)?(\s*[-/]\s*\d{2,3}(\.\d)?)*\s*kv", re.I)
# an endpoint name ends where the equipment / work description starts
_STOP = re.compile(
    r"\s+(BUS(ES)?|JUMPERS?|REPLACEMENT|CAPACITOR|RELAY|BREAKERS?|DUAL|SECOND|THIRD|REBUILDS?|REBLD|"
    r"TRANSMISSION|EQUIPMENT|IMPROVEMENTS|STATCOM|SWITCH(ES)?|AUTO|TRANSFORMERS?|AUTOBANKS?|BANKS?|NETWORK|"
    r"CAPACITY|SHUNT|REACTORS?|SERIES|PARALLEL|LOOP|INSTALLATION|MODERNIZATION|LOW SIDE|AREA|SOLUTION|"
    r"CONVERSION|RECONDUCTOR|NEW|EXPANSION|UPGRADES?|TAP|SUB|SUBSTATION|SWITCHING|LINES?|PROJECT|"
    r"STRATEGIC|WHITE|BLACK|SECTION|CONSTRUCT(ION)?|TIE|FOLD|DISTRIBUTION|RETIREMENT|ADD|INSTALL)\b.*$"
)


def haversine_mi(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def norm(name: str) -> str:
    s = name.upper().replace("&", " AND ")
    s = re.sub(r"\bST\.?(?=\s*$)", "STREET", s)  # trailing "St" = Street, leading = Saint
    s = re.sub(r"\bST\.?\s", "SAINT ", s)
    s = re.sub(r"\bPRI\b", "PRIMARY", s)
    s = re.sub(r"\bJCT\b", "JUNCTION", s)
    s = re.sub(r"\bFT\b", "FORT", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(SUBSTATION|SUB|SWITCHING STATION|STATION|SS|TS|DS|PLANT|ELECTRIC GENERATING)\b", " ", s)
    return " ".join(s.split())


def endpoints(project_name: str) -> list[str]:
    """'SAV: GOSHEN (SAV) - MCINTOSH 115KV LINE REBUILD' -> ['GOSHEN', 'MCINTOSH']"""
    s = _PREFIX.sub("", project_name.strip())
    s = s.split(":")[0]  # DESC style "A - B 115kV: Rebuild"
    s = re.sub(r"\(.*?\)|#\s*\d+|\bfold-in\b", " ", s, flags=re.I)
    s = _KV.sub(" | ", s)  # voltage marks the end of the endpoint names
    s = s.split("|")[0]
    s = re.split(r",|&| AND |/", s, flags=re.I)[0]  # drop second clause
    out = []
    for p in re.split(r"\s*[-–]\s*", s):
        p = norm(p)
        p = _STOP.sub("", p).strip()
        p = re.sub(r"\b\d+\b", " ", p).strip()
        if len(p) >= 3 and p not in out:
            out.append(p)
    return out[:2]


def load_osm() -> pd.DataFrame:
    els = json.loads(OSM_CACHE.read_text())["elements"]
    rows = []
    for e in els:
        tags = e.get("tags", {})
        if not tags.get("name"):
            continue
        lat = e.get("lat") or e.get("center", {}).get("lat")
        lon = e.get("lon") or e.get("center", {}).get("lon")
        if lat is None:
            continue
        rows.append({
            "osm_id": f"{e['type']}/{e['id']}", "osm_name": tags["name"], "key": norm(tags["name"]),
            "operator": tags.get("operator", ""), "kind": tags.get("power", ""), "lat": lat, "lon": lon,
        })
    return pd.DataFrame(rows)


# Approximate GA/SC border (Savannah River) and SC/NC border as (lat, lon) polylines.
_SAVANNAH_RIVER = [(35.21, -83.11), (34.68, -83.05), (34.35, -82.83), (34.00, -82.60), (33.66, -82.20),
                   (33.47, -81.97), (33.20, -81.75), (32.90, -81.50), (32.50, -81.26), (32.35, -81.15), (32.22, -81.13), (32.08, -81.09), (31.95, -80.85)]
_NC_BORDER = [(-83.11, 35.21), (-80.93, 35.10), (-79.68, 34.80), (-78.54, 33.85)]  # (lon, lat)


def _interp(x, pts):
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if min(x0, x1) <= x <= max(x0, x1):
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


def in_state(lat: float, lon: float, state: str) -> bool:
    river_lon = _interp(lat, _SAVANNAH_RIVER)
    if state == "SC":
        nc_lat = _interp(lon, _NC_BORDER)
        return (river_lon is not None and lon > river_lon and lon < -78.5
                and (nc_lat is None or lat < nc_lat))
    if state == "GA":
        west_of_river = river_lon is None or lon < river_lon
        fl_ok = lat > 30.36 and (lon < -82.2 or lat > 30.55)  # St Marys River bulges north near Jacksonville
        return fl_ok and lat < 35.0 and -85.61 < lon < -80.75 and west_of_river
    return True


def candidates(endpoint: str, osm: pd.DataFrame, utility: str, state: str | None = None, k: int = 6) -> list[dict]:
    op_re = UTILITY_OPERATORS.get(utility)
    if state:
        osm = osm[[in_state(a, o, state) for a, o in zip(osm.lat, osm.lon)]]
    exact = osm[osm.key == endpoint]
    if exact.empty:
        exact = osm[osm.key.str.startswith(endpoint + " ") | (osm.key == endpoint + " PRIMARY")]
    scored = []
    pool = exact if not exact.empty else osm[osm.key.str[:3] == endpoint[:3]]
    for r in pool.itertuples():
        sim = 1.0 if r.key == endpoint else SequenceMatcher(None, endpoint, r.key).ratio()
        if not exact.empty:
            sim = max(sim, 0.95)
        if sim < 0.85:
            continue
        op_ok = bool(op_re and op_re.search(r.operator or ""))
        scored.append({**r._asdict(), "sim": sim, "op_ok": op_ok})
    scored.sort(key=lambda c: (c["sim"], c["op_ok"]), reverse=True)
    return scored[:k]


NOMINATIM_CACHE = ROOT / "data" / "cache" / "nominatim_v2.json"
_ROADISH = re.compile(r"(STREET|ROAD|RD|DRIVE|AVENUE|PARKWAY|HIGHWAY|WAY|LANE|FERRY)")


def _plausible(hit: dict, endpoint: str) -> bool:
    """Accept towns/places and mapped power features; roads only when the
    substation is itself named after a road. Rejects courthouses, shops, etc."""
    cls = hit.get("class")
    if cls in ("power", "place", "boundary"):
        return True
    return cls == "highway" and bool(_ROADISH.search(endpoint))
_STATE_NAME = {"SC": "South Carolina", "GA": "Georgia"}


def nominatim(endpoint: str, state: str) -> list[dict]:
    """Fallback: geocode the endpoint as a place name. Always low confidence -
    a town centroid can be miles from the actual substation."""
    import time
    import requests

    cache = json.loads(NOMINATIM_CACHE.read_text()) if NOMINATIM_CACHE.exists() else {}
    key = f"{state}|{endpoint}"
    if key not in cache:
        hits = []
        for q in (f"{endpoint.title()} Substation, {_STATE_NAME[state]}", f"{endpoint.title()}, {_STATE_NAME[state]}"):
            time.sleep(1.1)  # Nominatim usage policy: max 1 request/second
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"q": q, "format": "json", "limit": 3, "countrycodes": "us"},
                             headers={"User-Agent": "shellhacks-gridlock/0.1 (hackathon project)"}, timeout=30)
            hits = [h for h in r.json() if in_state(float(h["lat"]), float(h["lon"]), state) and _plausible(h, endpoint)]
            if hits:
                break
        cache[key] = [{"lat": float(h["lat"]), "lon": float(h["lon"]), "display": h["display_name"]} for h in hits[:1]]
        NOMINATIM_CACHE.write_text(json.dumps(cache, indent=1))
    return [{"osm_name": h["display"][:60], "osm_id": "nominatim", "lat": h["lat"], "lon": h["lon"],
             "sim": 0.5, "op_ok": False, "fallback": True} for h in cache[key]]


def _confidence(c: dict) -> str:
    if c.get("fallback"):
        return "low"
    if c["sim"] >= 0.95 and c["op_ok"]:
        return "high"
    if c["sim"] >= 0.95:
        return "medium"
    return "low"


def geocode(projects: pd.DataFrame, osm: pd.DataFrame | None = None) -> pd.DataFrame:
    osm = load_osm() if osm is None else osm
    overrides = pd.read_csv(OVERRIDES) if OVERRIDES.exists() else pd.DataFrame(columns=["endpoint", "utility", "zone", "lat", "lon", "note"])
    # zone is optional: blank applies to every project of that utility
    ov = {(r.utility, r.endpoint, "" if pd.isna(r.zone) else str(int(r.zone))): r for r in overrides.itertuples()}
    state_osm = {s: osm[[in_state(a, o, s) for a, o in zip(osm.lat, osm.lon)]] for s in projects.state.unique()}

    out = []
    for p in projects.itertuples():
        eps = endpoints(p.project_name)
        cand_lists = []
        for ep in eps:
            zone = "" if pd.isna(p.zone) else str(int(p.zone))
            o = ov.get((p.utility, ep, zone)) or ov.get((p.utility, ep, ""))
            if o is not None:
                cand_lists.append([{"osm_name": ep, "osm_id": "override", "lat": o.lat, "lon": o.lon,
                                    "sim": 1.0, "op_ok": True, "manual": True, "note": o.note}])
            else:
                cands = candidates(ep, state_osm[p.state], p.utility)
                cand_lists.append(cands or nominatim(ep, p.state))

        # choose the combination that is most plausible: best names, operator agrees,
        # and (for two-ended projects) endpoints within a sane distance of each other
        best, best_score = [None] * len(eps), -1e9
        pools = [cl if cl else [None] for cl in cand_lists]
        for combo in product(*pools):
            score = sum((c["sim"] + 0.3 * c["op_ok"]) for c in combo if c)
            located = [c for c in combo if c]
            if len(located) == 2:
                gap = haversine_mi(located[0]["lat"], located[0]["lon"], located[1]["lat"], located[1]["lon"])
                if gap > MAX_ENDPOINT_GAP_MI:
                    score -= 5
                score -= gap / 200
            if score > best_score:
                best, best_score = list(combo), score

        # a place-name fallback far from the other (better-matched) end is almost
        # certainly a same-named town elsewhere: keep only the trustworthy end
        if len(best) == 2 and all(best):
            gap = haversine_mi(best[0]["lat"], best[0]["lon"], best[1]["lat"], best[1]["lon"])
            if gap > FALLBACK_MAX_GAP_MI:
                weaker = min((0, 1), key=lambda i: (not best[i].get("fallback"), best[i]["sim"]))
                if best[weaker].get("fallback") or gap > MAX_ENDPOINT_GAP_MI:
                    best[weaker] = None

        row = p._asdict()
        row.pop("Index", None)
        for tag, ep, c in zip("ab", eps + [None] * 2, best + [None] * 2):
            row[f"name_{tag}"] = ep
            row[f"lat_{tag}"] = c["lat"] if c else None
            row[f"lon_{tag}"] = c["lon"] if c else None
            row[f"match_{tag}"] = c["osm_name"] if c else None
            row[f"conf_{tag}"] = ("manual" if c.get("manual") else _confidence(c)) if c else None
        out.append(row)

    df = pd.DataFrame(out)
    for ax in ("lat", "lon"):
        df[f"{ax}_center"] = df[[f"{ax}_a", f"{ax}_b"]].mean(axis=1)  # one located end -> that end
    order = {"manual": 3, "high": 3, "medium": 2, "low": 1}
    df["confidence"] = df.apply(
        lambda r: min((order[c] for c in (r.conf_a, r.conf_b) if isinstance(c, str)), default=0), axis=1
    ).map({3: "high", 2: "medium", 1: "low", 0: "unlocated"})
    return df


if __name__ == "__main__":
    projects = pd.read_csv(ROOT / "data" / "processed" / "projects_raw.csv", parse_dates=["in_service_date"])
    df = geocode(projects)
    df.to_csv(ROOT / "data" / "processed" / "projects.csv", index=False)
    print(df.groupby(["utility", "confidence"]).size())
