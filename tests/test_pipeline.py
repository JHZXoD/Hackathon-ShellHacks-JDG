import pandas as pd
import pytest

from gridlock.geocode import endpoints, in_state
from gridlock.overlap import find_overlaps, load_projects, tier_for

A, B = "Dominion Energy South Carolina", "Georgia Power"


@pytest.mark.parametrize("name,expected", [
    ("SAV: GOSHEN (SAV) - MCINTOSH 115KV LINE REBUILD", ["GOSHEN", "MCINTOSH"]),
    ("EVANS PRIMARY - THURMOND DAM (USA) #5 115KV REBUILD", ["EVANS PRIMARY", "THURMOND DAM"]),
    ("Jasper – Okatie 230 kV #2: Construct", ["JASPER", "OKATIE"]),
    ("Stevens Creek - Hooks 115kV/LR Plumb Branch 46kV Rebuilds", ["STEVENS CREEK", "HOOKS"]),
    ("Burton-St Helena 115kV: Frogmore Distribution - St Helena", ["BURTON", "SAINT HELENA"]),
    ("PINE GROVE PRIMARY 115 KV DUAL STAGE CAPACITOR BANK", ["PINE GROVE PRIMARY"]),
])
def test_endpoints(name, expected):
    assert endpoints(name) == expected


def test_state_border():
    assert in_state(32.33, -81.03, "SC")      # Okatie, SC
    assert not in_state(32.35, -81.18, "SC")  # McIntosh, GA side of the river
    assert in_state(32.35, -81.18, "GA")
    assert not in_state(30.37, -81.68, "GA")  # Jacksonville, FL
    assert not in_state(34.47, -85.34, "SC")  # Summerville, GA


def test_tiers():
    assert tier_for(0.0)[0] == "Touching / crossing"
    assert tier_for(1.0)[0] == "Under 1.6 km"
    assert tier_for(5.0)[0] == "Under 8 km"
    assert tier_for(39.9)[0] == "Under 40 km"
    assert tier_for(40.1) is None


@pytest.fixture(scope="module")
def overlaps():
    return find_overlaps(load_projects(), A, B)


# Sperry's answer key (Projects_Overlaps.xlsx), center-to-center miles.
# Their GPC_3 McIntosh point differs slightly from GPC_2's, hence the tolerance.
SPERRY_KEY = [
    ("Hooks - Thurmond", "EVANS PRIMARY - THURMOND DAM (USA) #5", 4.09),
    ("Jasper – Okatie 230 kV #2", "MCINTOSH - PURRYSBURG", 5.65),
    ("Jasper – Okatie 230 kV #2", "GOSHEN (SAV) - MCINTOSH", 7.55),
    ("Stevens Creek - Hooks 115kV/LR Plumb Branch 46kV Rebuilds", "EVANS PRIMARY - THURMOND DAM (USA) #5", 8.01),
    ("Okatie-Bluffton", "MCINTOSH - PURRYSBURG", 14.34),
    ("Okatie-Bluffton", "GOSHEN (SAV) - MCINTOSH", 14.81),
]


@pytest.mark.parametrize("name_a,name_b,center_mi", SPERRY_KEY)
def test_matches_sperry_answer_key(overlaps, name_a, name_b, center_mi):
    hit = overlaps[overlaps.project_name_a.str.contains(name_a, regex=False)
                   & overlaps.project_name_b.str.contains(name_b, regex=False)]
    assert not hit.empty, f"missing overlap {name_a} x {name_b}"
    assert hit.center_distance_mi.iloc[0] == pytest.approx(center_mi, abs=0.3)


def test_no_far_pairs_and_ranked(overlaps):
    assert (overlaps.distance_km <= 40).all()
    assert overlaps.score.is_monotonic_decreasing
    top = overlaps.iloc[0]
    assert "Jasper" in top.project_name_a and "MCINTOSH" in top.project_name_b
