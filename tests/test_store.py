import pandas as pd

from gridlock.overlap import find_overlaps, load_projects
from gridlock.store import apply

A, B = "Dominion Energy South Carolina", "Georgia Power"


def _v(**kw):
    base = {"action": "correct", "name": "tester", "created_at": pd.Timestamp.now(tz="UTC")}
    return {**base, **kw}


def test_no_verifications_is_noop():
    p = load_projects()
    assert apply(p, pd.DataFrame()) is p


def test_correction_moves_point_and_updates_center():
    p = load_projects()
    pid = p[p.project_name.str.contains("Jasper – Okatie 230 kV #2", regex=False)].project_id.iloc[0]
    out = apply(p, pd.DataFrame([_v(project_id=pid, endpoint="a", lat=32.4, lon=-81.0)]))
    r = out.set_index("project_id").loc[pid]
    assert (r.lat_a, r.lon_a) == (32.4, -81.0)
    assert r.conf_a == "verified"
    assert r.lat_center == (32.4 + r.lat_b) / 2


def test_latest_verification_wins():
    p = load_projects()
    pid = p.project_id.iloc[0]
    v = pd.DataFrame([_v(project_id=pid, endpoint="a", lat=33.0, lon=-80.0),
                      _v(project_id=pid, endpoint="a", lat=33.5, lon=-80.5)])
    assert apply(p, v).set_index("project_id").loc[pid].lat_a == 33.5


def test_placing_unlocated_project_adds_overlap():
    p = load_projects()
    un = p[(p.confidence == "unlocated") & (p.utility == B)].project_id.iloc[0]
    # drop it right next to the Jasper substation near Savannah
    out = apply(p, pd.DataFrame([_v(project_id=un, endpoint="a", lat=32.36, lon=-81.13)]))
    assert out.set_index("project_id").loc[un].confidence == "verified"
    ov = find_overlaps(out, A, B)
    assert un in set(ov.project_id_b)
