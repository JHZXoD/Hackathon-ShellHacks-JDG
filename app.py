"""GridNeighbors - find where neighboring utilities' planned transmission work overlaps.

Run:  streamlit run app.py
"""
from __future__ import annotations

import folium
import pandas as pd
import streamlit as st
from shapely.ops import nearest_points
from streamlit_folium import st_folium

from gridlock import brief, store
from gridlock.cost import Assumptions, estimate
from gridlock.geocode import in_state
from gridlock.overlap import TIERS, find_overlaps, geometry, load_projects

UTIL_A, UTIL_B = "Dominion Energy South Carolina", "Georgia Power"
COLORS = {UTIL_A: "#2563eb", UTIL_B: "#ea580c"}
SHORT = {UTIL_A: "DESC", UTIL_B: "Georgia"}
TIER_COLORS = {"Touching / crossing": "#dc2626", "Under 1.6 km": "#e11d48", "Under 8 km": "#f59e0b", "Under 40 km": "#a3a3a3"}
CONF_ORDER = {"verified": 3, "high": 3, "medium": 2, "low": 1, "unlocated": 0}

st.set_page_config(page_title="GridNeighbors", page_icon="⚡", layout="wide")


@st.cache_data
def data():
    return load_projects()


@st.cache_data(ttl=60)
def verifications() -> pd.DataFrame:
    """Community location checks from MongoDB; the app still works if Atlas is unreachable."""
    try:
        return store.load_all()
    except Exception:
        return pd.DataFrame()


@st.cache_data
def overlaps_for(projects: pd.DataFrame) -> pd.DataFrame:
    return find_overlaps(projects, UTIL_A, UTIL_B)


def md(text: str) -> str:
    """Escape $ so Streamlit markdown doesn't render dollar amounts as LaTeX."""
    return text.replace("$", r"\$")


def fmt_usd(x: float) -> str:
    if x >= 1e6:
        return f"${x / 1e6:,.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:,.0f}K"
    return f"${x:,.2f}"


def parse_latlon(text: str) -> tuple[float, float] | None:
    """Accepts '32.35, -81.17' (the format Google Maps copies on right-click)."""
    try:
        lat, lon = (float(x) for x in text.replace("(", "").replace(")", "").split(",")[:2])
        return lat, lon
    except ValueError:
        return None


def maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@{lat},{lon},17z/data=!3m1!1e3"  # satellite view


def verify_widget(p: pd.Series, key: str) -> None:
    """Confirm or correct each endpoint of one project; saved to MongoDB for everyone."""
    for t in "ab":
        name = p[f"name_{t}"]
        if not isinstance(name, str):
            continue
        has_pt = pd.notna(p[f"lat_{t}"])
        where = (f"[{p[f'lat_{t}']:.5f}, {p[f'lon_{t}']:.5f}]({maps_link(p[f'lat_{t}'], p[f'lon_{t}'])})"
                 if has_pt else "not located yet")
        match = p[f"match_{t}"] if isinstance(p[f"match_{t}"], str) else "none"
        conf = p[f"conf_{t}"] if isinstance(p[f"conf_{t}"], str) else "unlocated"
        conf = {"manual": "hand-verified"}.get(conf, conf)
        st.markdown(f"**{name.title()}** ({SHORT[p.utility]}) · matched: *{match}* · confidence: `{conf}` · {where}")
        with st.form(f"verify_{key}_{p.project_id}_{t}", clear_on_submit=True, border=False):
            c1, c2, c3, c4 = st.columns([2, 3, 2, 1.3])
            options = ["Location is correct", "Move it to these coordinates"] if has_pt else ["Place it at these coordinates"]
            action = c1.radio("Action", options, label_visibility="collapsed")
            coords = c2.text_input("Coordinates", placeholder="lat, lon (right-click in Google Maps to copy)",
                                   label_visibility="collapsed")
            who = c3.text_input("Your name", placeholder="Your name (optional)", label_visibility="collapsed")
            submitted = c4.form_submit_button("Save", width="stretch")
        if submitted:
            if action == "Location is correct":
                lat, lon, kind = p[f"lat_{t}"], p[f"lon_{t}"], "confirm"
            else:
                parsed = parse_latlon(coords)
                if not parsed:
                    st.error("Enter coordinates as `lat, lon`, e.g. `32.3521, -81.1751`.")
                    continue
                (lat, lon), kind = parsed, "correct"
                # tie lines can end across the river (e.g. Purrysburg, SC on a Georgia project), so allow either state
                if not (in_state(lat, lon, "SC") or in_state(lat, lon, "GA")):
                    st.error("Those coordinates are outside South Carolina and Georgia. Check the lat/lon order.")
                    continue
            prev = (p[f"lat_{t}"], p[f"lon_{t}"]) if has_pt else None
            store.save(p.project_id, t, kind, lat, lon, name=who, previous=prev)
            verifications.clear()
            st.toast("Saved to the shared database. The map and rankings now use it.", icon="✅")
            st.rerun()


projects = store.apply(data(), verifications())

# ---------------- sidebar filters ----------------
with st.sidebar:
    st.header("Filters")
    max_km = st.slider("Max distance between projects (km)", 0.5, 40.0, 40.0, 0.5,
                       help="Closest-point distance. Sperry's overlap threshold is 40 km (25 mi).")
    max_gap_yrs = st.slider("Max gap between in-service dates (years)", 0.0, 11.0, 11.0, 0.5)
    min_conf = st.select_slider("Minimum location confidence", ["low", "medium", "high"], "low")
    sponsors = sorted(projects.loc[projects.utility == UTIL_B, "sponsor"].dropna().unique())
    chosen = st.multiselect("Georgia plan sponsors", sponsors, default=sponsors,
                            help="The Georgia ITS 10-year plan includes projects sponsored by Georgia Power (GPC, SAV = "
                                 "Savannah area) and its planning partners (GTC, MEAG, DU).")
    st.divider()
    st.caption("Distances: closest point between project geometries (a straight line between the two endpoint "
               "substations, or a point). Center-to-center distance is also shown.")

in_scope = projects[(projects.utility == UTIL_A) | projects.sponsor.isin(chosen)]
visible = in_scope[in_scope.confidence.map(CONF_ORDER) >= CONF_ORDER[min_conf]]
ov = overlaps_for(visible)
if not ov.empty:
    ov = ov[(ov.distance_km <= max_km) & (ov.time_gap_days.fillna(0) <= max_gap_yrs * 365)].copy()
    ov["rank"] = range(1, len(ov) + 1)

# ---------------- header ----------------
st.title("⚡ GridNeighbors")
st.markdown("**Where neighboring utilities are planning grid work in the same place, at the same time**: "
            "Dominion Energy South Carolina vs. the Georgia Power 10-year transmission plan.")

located = visible[visible.confidence != "unlocated"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Planned projects", len(in_scope), f"{len(located)} shown on map", delta_color="off")
c2.metric("Coordination opportunities", len(ov))
c3.metric("Closer than 8 km", int((ov.distance_km < 8).sum()) if len(ov) else 0)
c4.metric("Build within 1 year of each other", int((ov.time_gap_days <= 365).sum()) if len(ov) else 0)

tab_ops, tab_projects, tab_method = st.tabs(["Coordination opportunities", "All projects", "Method & data quality"])

# ---------------- opportunities ----------------
with tab_ops:
    if ov.empty:
        st.info("No overlaps with the current filters.")
        st.stop()

    table = ov[["rank", "project_name_a", "project_name_b", "distance_mi", "tier", "time_gap_days",
                "in_service_a", "in_service_b", "confidence_a", "confidence_b", "score"]].rename(columns={
        "project_name_a": "DESC project", "project_name_b": "Georgia project", "distance_mi": "Distance (mi)",
        "tier": "Tier", "time_gap_days": "Timeline gap (days)", "in_service_a": "DESC in-service",
        "in_service_b": "GA in-service", "confidence_a": "DESC loc.", "confidence_b": "GA loc.", "score": "Score"})

    left, right = st.columns([3, 2], gap="medium")
    with right:
        st.subheader("Ranked opportunities")
        event = st.dataframe(table, hide_index=True, width="stretch", height=520,
                             on_select="rerun", selection_mode="single-row",
                             column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")})
        sel_rows = event.selection.rows if event and event.selection else []
        sel = ov.iloc[sel_rows[0]] if sel_rows else ov.iloc[0]
        st.caption("Click a row to focus it on the map. Score = distance tier (primary) + timeline gap (secondary), "
                   "discounted for low-confidence locations.")

    with left:
        pair_ids = set(ov.project_id_a) | set(ov.project_id_b)
        focus = {sel.project_id_a, sel.project_id_b}
        fmap = folium.Map(location=[32.9, -81.4], zoom_start=7, tiles="OpenStreetMap", control_scale=True)
        idx = located.set_index("project_id")

        for pid, r in idx.iterrows():
            g = geometry(r)
            in_pair, is_focus = pid in pair_ids, pid in focus
            color = COLORS[r.utility]
            date = r.in_service_date.date() if pd.notna(r.in_service_date) else "n/a"
            cost = fmt_usd(r.est_cost_usd) if pd.notna(r.est_cost_usd) else "redacted"
            popup_html = (f"<b>{r.project_name}</b><br>{r.utility} ({r.sponsor})<br>In service: {date}"
                                 f"<br>Cost: {cost}<br>Location confidence: {r.confidence}<br>"
                                 f"<small>{r.source}, p.{r.source_page}</small>")
            opacity = 1.0 if is_focus else 0.85 if in_pair else 0.25
            if g.geom_type == "LineString":
                folium.PolyLine([(y, x) for x, y in g.coords], color=color, weight=7 if is_focus else 4 if in_pair else 2,
                                opacity=opacity, tooltip=r.project_name,
                                popup=folium.Popup(popup_html, max_width=320)).add_to(fmap)
            for t in "ab":
                if pd.notna(r[f"lat_{t}"]):
                    folium.CircleMarker([r[f"lat_{t}"], r[f"lon_{t}"]], radius=6 if is_focus else 4, color=color,
                                        fill=True, fill_opacity=opacity, opacity=opacity,
                                        tooltip=f"{r[f'name_{t}']} ({SHORT[r.utility]}, {r[f'conf_{t}']})",
                                        popup=folium.Popup(popup_html, max_width=320)).add_to(fmap)

        for _, o in ov.iterrows():
            pa, pb = nearest_points(geometry(idx.loc[o.project_id_a]), geometry(idx.loc[o.project_id_b]))
            is_sel = o.overlap_id == sel.overlap_id
            folium.PolyLine([(pa.y, pa.x), (pb.y, pb.x)], color=TIER_COLORS[o.tier], weight=4 if is_sel else 2,
                            dash_array="6 6", opacity=1 if is_sel else 0.6,
                            tooltip=f"#{o['rank']}: {o.distance_mi} mi, {o.tier}").add_to(fmap)

        ga, gb = geometry(idx.loc[sel.project_id_a]), geometry(idx.loc[sel.project_id_b])
        minx, miny, maxx, maxy = ga.union(gb).bounds
        pad = 0.08
        fmap.fit_bounds([[miny - pad, minx - pad], [maxy + pad, maxx + pad]])
        legend = "".join(f'<span style="color:{c}">&#9632;</span> {u}<br>' for u, c in COLORS.items())
        legend += "".join(f'<span style="color:{c}">- -</span> {t}<br>' for t, c in TIER_COLORS.items())
        fmap.get_root().html.add_child(folium.Element(
            f'<div style="position:fixed;bottom:24px;left:12px;z-index:9999;background:white;padding:8px 10px;'
            f'border-radius:6px;font-size:12px;box-shadow:0 1px 4px rgba(0,0,0,.3)">{legend}</div>'))
        st_folium(fmap, height=560, use_container_width=True, returned_objects=[])

    # ---------------- detail panel ----------------
    st.divider()
    st.subheader(f"#{sel['rank']}: {sel.project_name_a}  ×  {sel.project_name_b}")
    share = dict((label, s) for _, label, s in TIERS)[sel.tier]
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Closest distance", f"{sel.distance_mi} mi", f"{sel.distance_km} km", delta_color="off")
    d2.metric("Center-to-center", f"{sel.center_distance_mi} mi")
    d3.metric("Timeline gap", f"{int(sel.time_gap_days)} days" if pd.notna(sel.time_gap_days) else "n/a",
              f"{sel.in_service_a} vs {sel.in_service_b}", delta_color="off")
    d4.metric("Tier", sel.tier)
    st.markdown(f"**What they could share:** {share}")

    with st.expander("Cost assumptions (edit to see how the estimate changes)"):
        a = Assumptions()
        e1, e2, e3 = st.columns(3)
        a.mobilization_share = e1.slider("Mobilization as % of project cost", 2, 15, 8) / 100
        a.easement_cost_per_acre = e2.number_input("Easement cost per acre ($)", 1000, 100000, 12000, 1000)
        a.cost_per_mile_usd = e3.number_input("Line cost per mile when redacted ($)", 250_000, 5_000_000, 1_500_000, 250_000)

    est = estimate(sel, projects, a)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Estimated savings", fmt_usd(est["total_savings_usd"]))
    k2.metric("Shared mobilization", fmt_usd(est["mobilization_savings_usd"]),
              f"tier x{est['tier_share']}, timing x{est['timing_factor']}", delta_color="off")
    k3.metric("Shared right-of-way", f"{est['shared_acres']} acres", fmt_usd(est["row_savings_usd"]), delta_color="off")
    k4.metric("Per customer (both utilities)", f"${est['savings_per_customer_usd']:.3f}")
    st.caption(md(f"DESC project cost: {fmt_usd(est['cost_a_usd'])} ({est['cost_a_source']}). "
               f"Georgia project cost: {fmt_usd(est['cost_b_usd'])} ({est['cost_b_source']}; Georgia costs are "
               f"redacted in the public filing)."))
    if est["rate_base_note"]:
        st.info(md(f"💡 **Why this matters for bills:** {est['rate_base_note']}"))

    total_all = sum(estimate(r, projects, a)["total_savings_usd"] for _, r in ov.iterrows())
    st.caption(md(f"Across all {len(ov)} opportunities shown, estimated coordination value is **{fmt_usd(total_all)}**."))

    st.markdown("#### Verify these locations")
    if store.available():
        st.caption("Planners know where their substations are. Confirm or correct a location and it's saved to a shared "
                   "MongoDB database, so every visitor's map, distances and rankings immediately use it.")
        by_id = projects.set_index("project_id", drop=False)
        vc1, vc2 = st.columns(2)
        with vc1:
            verify_widget(by_id.loc[sel.project_id_a], "pair")
        with vc2:
            verify_widget(by_id.loc[sel.project_id_b], "pair")
    else:
        st.caption("Add MONGODB_URI to .env to let users confirm or correct locations.")

    st.markdown("#### AI coordination brief")
    if brief.available():
        facts = brief.facts_for(sel, share, est)
        briefs = st.session_state.setdefault("briefs", {})
        if st.button("Generate brief with Gemini", type="primary"):
            with st.spinner("Writing brief..."):
                try:
                    briefs[sel.overlap_id] = brief.brief(facts)
                except Exception as exc:  # show the failure, keep the app usable
                    st.error(f"Gemini request failed: {exc}")
        if sel.overlap_id in briefs:  # stays visible while the user tweaks filters/assumptions
            st.markdown(md(briefs[sel.overlap_id]))
    else:
        st.caption("Add GEMINI_API_KEY to .env to generate a plain-English brief for planners.")

# ---------------- all projects ----------------
with tab_projects:
    cols = ["project_id", "utility", "sponsor", "project_name", "in_service_date", "est_cost_usd", "voltage_kv",
            "name_a", "match_a", "conf_a", "name_b", "match_b", "conf_b", "confidence", "source", "source_page"]
    st.dataframe(in_scope[cols], hide_index=True, width="stretch", height=600)

# ---------------- method ----------------
with tab_method:
    st.markdown("""
**Data.** Dominion Energy South Carolina: *Planned Transmission Projects $2M and above* (44 projects, with costs and
rate-base year). Georgia: *Georgia ITS 10-Year Plan project list* in Georgia Power's 2025 IRP Volume 3, public
disclosure version (costs redacted). Only public filings are used.

**Locations.** Each project's endpoint substations are parsed from its name and matched to OpenStreetMap substations
and power plants in SC/GA. A state-border check (Savannah River) rejects same-named substations in the wrong state.
Unmatched names fall back to OSM place search. Hand-verified points come from Sperry's starter file.

| Confidence | Meaning |
|---|---|
| verified | confirmed or corrected by a user (stored in MongoDB Atlas) |
| high | exact substation name match, operator agrees (or hand-verified) |
| medium | exact name match, operator not tagged |
| low | fuzzy or place-name match; could be miles off |
| unlocated | no trustworthy match; not on the map |

**Overlap.** Closest-point distance between project geometries (lines between endpoints, or points) in an
equal-area projection. Flagged under 40 km (25 mi), tiered at touching, 1.6 km, 8 km and 40 km. Timeline gap = days
between planned in-service dates. **Score** = distance (primary) + timeline (secondary), discounted by location
confidence.

**Validation.** The automated test suite checks the pipeline against all 6 overlaps in Sperry's answer key.
""")
    conf = projects.groupby(["utility", "confidence"]).size().unstack(fill_value=0)
    st.dataframe(conf, width="stretch")
    v = verifications()
    st.markdown("#### Community verifications")
    if v.empty:
        st.caption("No locations verified yet. Confirm or correct one in an opportunity's detail panel.")
    else:
        names = projects.set_index("project_id").project_name
        endpoints = v.groupby(["project_id", "endpoint"]).ngroups
        m1, m2, m3 = st.columns(3)
        m1.metric("Verifications", len(v))
        m2.metric("Substations verified", endpoints)
        m3.metric("Corrections", int((v.action == "correct").sum()))
        log = v.sort_values("created_at", ascending=False).head(25).assign(
            project=lambda d: d.project_id.map(names),
            when=lambda d: pd.to_datetime(d.created_at).dt.strftime("%b %d %H:%M UTC"))
        st.dataframe(log[["when", "project", "endpoint", "action", "lat", "lon", "name"]], hide_index=True, width="stretch")

    st.markdown("#### Help locate a project")
    unlocated = projects[projects.confidence == "unlocated"]
    st.caption(f"{len(unlocated)} projects couldn't be placed automatically. If you know where one is, place it and it "
               "joins the overlap analysis for everyone.")
    if store.available() and len(unlocated):
        pick = st.selectbox("Project", unlocated.project_id,
                            format_func=lambda pid: f"{pid}: {unlocated.set_index('project_id').project_name[pid]}")
        verify_widget(unlocated.set_index("project_id", drop=False).loc[pick], "locate")
    else:
        st.dataframe(unlocated[["project_id", "utility", "project_name", "name_a", "name_b"]], hide_index=True, width="stretch")
