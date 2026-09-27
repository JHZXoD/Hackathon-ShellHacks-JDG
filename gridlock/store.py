"""User location verifications, stored in MongoDB Atlas.

Anyone reviewing an opportunity can confirm a matched substation location or
correct it. Verifications are shared: every later visitor's map, distances and
rankings use them, so the dataset gets more accurate the more it is used.
Without MONGODB_URI the app runs read-only on the pipeline's locations.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
COLLECTION = "location_verifications"

_client = None


def _collection():
    global _client
    uri = os.getenv("MONGODB_URI")
    if not uri:
        return None
    if _client is None:
        from pymongo import MongoClient

        _client = MongoClient(uri, serverSelectionTimeoutMS=8000, appname="gridneighbors")
    return _client["gridneighbors"][COLLECTION]


def available() -> bool:
    return bool(os.getenv("MONGODB_URI"))


def save(project_id: str, endpoint: str, action: str, lat: float, lon: float,
         name: str = "", note: str = "", previous: tuple | None = None) -> None:
    """action: 'confirm' (location is right) or 'correct' (moved to lat/lon)."""
    _collection().insert_one({
        "project_id": project_id,
        "endpoint": endpoint,  # 'a' or 'b'
        "action": action,
        "lat": float(lat),
        "lon": float(lon),
        "previous": [float(x) for x in previous] if previous else None,
        "name": name.strip()[:60] or "anonymous",
        "note": note.strip()[:280],
        "created_at": datetime.now(timezone.utc),
    })


def load_all() -> pd.DataFrame:
    col = _collection()
    if col is None:
        return pd.DataFrame()
    docs = list(col.find({}, {"_id": 0}).sort("created_at", 1))
    return pd.DataFrame(docs)


def apply(projects: pd.DataFrame, verifications: pd.DataFrame) -> pd.DataFrame:
    """Latest verification per endpoint wins: move the point if corrected, and
    mark the endpoint 'verified' either way. Recomputes centers + confidence."""
    if verifications.empty:
        return projects
    df = projects.copy()
    latest = verifications.groupby(["project_id", "endpoint"]).tail(1)
    idx = {pid: i for i, pid in df.project_id.items()}
    for v in latest.itertuples():
        i = idx.get(v.project_id)
        if i is None:
            continue
        df.at[i, f"lat_{v.endpoint}"] = v.lat
        df.at[i, f"lon_{v.endpoint}"] = v.lon
        df.at[i, f"conf_{v.endpoint}"] = "verified"
        if v.action == "correct":
            df.at[i, f"match_{v.endpoint}"] = f"corrected by {v.name}"
    for ax in ("lat", "lon"):
        df[f"{ax}_center"] = df[[f"{ax}_a", f"{ax}_b"]].mean(axis=1)
    order = {"verified": 3, "manual": 3, "high": 3, "medium": 2, "low": 1}
    df["confidence"] = df.apply(
        lambda r: min((order[c] for c in (r.conf_a, r.conf_b) if isinstance(c, str)), default=0), axis=1
    ).map({3: "high", 2: "medium", 1: "low", 0: "unlocated"})
    touched = set(latest.project_id)
    df.loc[df.project_id.isin(touched) & (df.confidence == "high"), "confidence"] = "verified"
    return df
