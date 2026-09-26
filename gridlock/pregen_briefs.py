"""Pre-generate Gemini briefs for the top opportunities (default filters and
assumptions) so the demo shows them instantly: python -m gridlock.pregen_briefs [N]"""
import sys

from gridlock import brief
from gridlock.cost import Assumptions, estimate
from gridlock.overlap import TIERS, find_overlaps, load_projects

UTIL_A, UTIL_B = "Dominion Energy South Carolina", "Georgia Power"

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    projects = load_projects()
    ov = find_overlaps(projects[projects.confidence != "unlocated"], UTIL_A, UTIL_B)
    shares = {label: s for _, label, s in TIERS}
    for _, pair in ov.head(n).iterrows():
        facts = brief.facts_for(pair, shares[pair.tier], estimate(pair, projects, Assumptions()))
        try:
            brief.brief(facts)
            status = "ok"
        except Exception as exc:  # congestion: skip, rerun later (finished ones are cached)
            status = f"skipped ({type(exc).__name__})"
        print(f"#{pair['rank']} {status}: {pair.project_name_a[:35]} x {pair.project_name_b[:35]}")
