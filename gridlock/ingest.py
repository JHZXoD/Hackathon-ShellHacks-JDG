"""Parse the two utilities' public project-list PDFs into one project table.

DESC: one project per page ("Planned Transmission Projects $2M and above").
GPC:  Table 2 "Georgia ITS 10 Year Plan Project List" (pages 177-190 of IRP Vol 3).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
LISTINGS = ROOT / "Sperry-Tech-Challenge" / "Project Listings"
DESC_PDF = LISTINGS / "Dominion Energy" / "2024-2028-2million-and-above-project-descriptions.pdf"
GPC_PDF = LISTINGS / "Georgia Power" / "2025 IRP Volume 3 PUBLIC DISCLOSURE.pdf"
GPC_TABLE_PAGES = range(177, 191)  # 1-indexed, inclusive of 190

COLUMNS = [
    "project_id", "utility", "state", "project_name", "description", "need",
    "status", "sponsor", "zone", "in_service_date", "est_cost_usd", "voltage_kv",
    "source", "source_page",
]

_DATE = r"\d{1,2}/\d{1,2}/\d{2,4}"


def _parse_date(s: str) -> pd.Timestamp | None:
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return pd.Timestamp(datetime.strptime(s, fmt))
        except ValueError:
            pass
    return None


def _max_kv(text: str) -> float | None:
    kvs = [float(k) for k in re.findall(r"(\d{2,3})(?:\s*-\s*\d{2,3})?\s*kv", text, re.I)]
    return max(kvs) if kvs else None


def _section(text: str, start: str, end: str) -> str:
    m = re.search(rf"{start}\s*\n(.*?)\n\s*{end}", text, re.S)
    return " ".join(m.group(1).split()) if m else ""


def parse_desc(pdf_path: Path = DESC_PDF) -> pd.DataFrame:
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            name = _section(text, r"5 Year Budget", r"Project ID")
            pid = _section(text, r"Project ID", r"Project Description")
            date_m = re.search(rf"Planned In-Service Date\s*\n\s*({_DATE})", text)
            costs = re.findall(r"\$[\d,]+", text)
            total = int(costs[-1].replace("$", "").replace(",", "")) if costs else None
            rows.append({
                "project_id": f"DESC_{page_no}",
                "utility": "Dominion Energy South Carolina",
                "state": "SC",
                "project_name": name,
                "description": _section(text, r"Project Description", r"Project Need"),
                "need": _section(text, r"Project Need", r"Project Status"),
                "status": _section(text, r"Project Status", r"Planned In-Service Date"),
                "sponsor": "DESC",
                "zone": None,
                "in_service_date": _parse_date(date_m.group(1)) if date_m else None,
                "est_cost_usd": total,
                "voltage_kv": _max_kv(name),
                "source": f"DESC SCRTP project list, ID {pid}",
                "source_page": page_no,
            })
    return pd.DataFrame(rows, columns=COLUMNS)


_GPC_ROW_START = re.compile(r"^(2\d\d)\s+(20\d\d)\s+(\d{4,5})\s+(.*)$")
_GPC_ROW_END = re.compile(rf"^(.*?)\s*({_DATE})\s+([A-Z]+)\s+REDACTED")


def parse_gpc(pdf_path: Path = GPC_PDF, pages=GPC_TABLE_PAGES) -> pd.DataFrame:
    # pypdf keeps wrapped name lines in reading order; pdfplumber puts the
    # vertically-centered date in the middle of multi-line names.
    rows = []
    reader = PdfReader(pdf_path)
    for page_no in pages:
            text = reader.pages[page_no - 1].extract_text() or ""
            current = None
            for line in text.splitlines():
                line = line.strip()
                start = _GPC_ROW_START.match(line)
                if start:
                    zone, _year, teams, rest = start.groups()
                    current = {"zone": zone, "teams": teams, "name_parts": [], "page": page_no}
                    line = rest
                if current is None:
                    continue
                end = _GPC_ROW_END.match(line)
                if end:
                    current["name_parts"].append(end.group(1))
                    name = " ".join(" ".join(current["name_parts"]).split())
                    rows.append({
                        "project_id": f"GPC_{current['teams']}",
                        "utility": "Georgia Power",
                        "state": "GA",
                        "project_name": name,
                        "description": None,
                        "need": None,
                        "status": "Planned",
                        "sponsor": end.group(3),
                        "zone": current["zone"],
                        "in_service_date": _parse_date(end.group(2)),
                        "est_cost_usd": None,  # redacted in the public filing
                        "voltage_kv": _max_kv(name),
                        "source": f"GA ITS 10-Year Plan (GPC 2025 IRP Vol. 3), TEAMS #{current['teams']}",
                        "source_page": current["page"],
                    })
                    current = None
                else:
                    current["name_parts"].append(line)
    return pd.DataFrame(rows, columns=COLUMNS)


def load_projects() -> pd.DataFrame:
    return pd.concat([parse_desc(), parse_gpc()], ignore_index=True)


if __name__ == "__main__":
    out = ROOT / "data" / "processed" / "projects_raw.csv"
    df = load_projects()
    df.to_csv(out, index=False)
    print(df.groupby(["utility", "sponsor"]).size())
    print(f"wrote {len(df)} projects -> {out}")
