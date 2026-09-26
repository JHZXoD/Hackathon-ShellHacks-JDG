# GridNeighbors

**Live:** https://gridneighbors.design

Finds where neighboring utilities are planning transmission work in the same place at the same time, ranks the
coordination opportunities, and estimates what coordinating could save customers.
Built for the Sperry Tech GridLock challenge at ShellHacks 2026.

**Utilities:** Dominion Energy South Carolina (SCRTP $2M+ project list) and the Georgia ITS 10-Year Plan
(Georgia Power 2025 IRP Vol. 3, public disclosure).

## Run

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/streamlit run app.py
```

Optional: copy `.env.example` to `.env` and add a Google AI Studio key to enable Gemini coordination briefs.

## Pipeline

| Step | Module | Output |
|---|---|---|
| Parse both utilities' PDFs | `gridlock/ingest.py` | `data/processed/projects_raw.csv` |
| Match endpoint substations to OpenStreetMap, grade confidence | `gridlock/geocode.py` | `data/processed/projects.csv` |
| Closest-point overlap, tiers, timeline gap, ranking | `gridlock/overlap.py` | `data/processed/overlaps.csv` |
| Cost / impact estimate | `gridlock/cost.py` | shown in app |
| Plain-English brief (Gemini) | `gridlock/brief.py` | shown in app |

Rebuilding the data needs the sponsor's `Sperry-Tech-Challenge/` folder (not committed) and network access to
OpenStreetMap (Overpass + Nominatim; results cached in `data/cache/`):

```bash
.venv/Scripts/python -m gridlock.ingest
.venv/Scripts/python -m gridlock.geocode
.venv/Scripts/python -m gridlock.overlap
.venv/Scripts/python -m pytest
```

Hand-verified coordinates go in `data/overrides.csv`.
