"""
EthioJobs Rollup — turns the flat Jobs tab into ready-to-chart summaries for
five time windows (daily / weekly / monthly / quarterly / yearly), written
out as one JSON file that the dashboard page (docs/index.html) fetches and
renders. This keeps the dashboard itself completely static (no server, no
database) — it just reads a JSON file that this script refreshes daily.

Windows are ROLLING, measured from "now" back by date_scraped (when our
pipeline first saw the posting) — not the site's own relative "posted X
days ago" text, which isn't reliable for date math. So "daily" means
"postings first seen in the last 24 hours," not "postings dated today."

Run this AFTER ethiojobs_pipeline.py in the same workflow, so it always
summarizes the freshly updated tracker.
"""

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import openpyxl

INPUT_XLSX = "ethiojobs_tracker.xlsx"
OUTPUT_JSON = "docs/data/rollups.json"

WINDOWS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}

CLEAN_EDU_ORDER = ["Bachelor's degree", "Master's degree", "Diploma", "Certificate", "PhD"]
DATE_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|"
                      r"October|November|December)\s+\d", re.I)
BAD_SKILL_LABELS = {"desired skill", "required skill", "skills", "skill", "other"}
SKILL_NORMALIZE = {
    "problem solving skill": "Problem solving",
    "problem solving": "Problem solving",
    "analytical thinking and problem-solving skills": "Problem solving",
    "decision-making skills": "Decision-making",
    "decision making skills": "Decision-making",
}
CATEGORY_EXPANSIONS = {
    "Social Sciences and Com": "Social Sciences and Communications",
}


def load_rows(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Jobs"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    rows = [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True)]
    wb.close()
    return rows


def parse_scraped(value):
    if not value:
        return None
    # stored as "YYYY-MM-DD HH:MM:SS UTC"
    try:
        return datetime.strptime(value.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def top_skills(rows, n=10):
    counter = Counter()
    display = {}
    for r in rows:
        if not r.get("skills_required"):
            continue
        for s in r["skills_required"].split(";"):
            s = s.strip(" •-\t.")
            if not s or len(s) < 3 or DATE_RE.search(s):
                continue
            key = s.lower().strip(".")
            if key in BAD_SKILL_LABELS:
                continue
            canon = SKILL_NORMALIZE.get(key, s)
            canon_key = canon.lower()
            counter[canon_key] += 1
            display.setdefault(canon_key, canon)
    return [{"label": display[k], "count": c} for k, c in counter.most_common(n)]


def education_breakdown(rows):
    counter = Counter(r.get("education_required") for r in rows if r.get("education_required") in CLEAN_EDU_ORDER)
    known_total = sum(counter.values())
    return {
        "known_total": known_total,
        "total": len(rows),
        "levels": [{"label": lvl, "count": counter.get(lvl, 0)} for lvl in CLEAN_EDU_ORDER if counter.get(lvl, 0) > 0],
    }


def top_categories(rows, n=10):
    counter = Counter()
    for r in rows:
        if not r.get("category"):
            continue
        for c in r["category"].split(";"):
            c = c.strip().rstrip(".")
            if not c:
                continue
            c = CATEGORY_EXPANSIONS.get(c, c)
            counter[c] += 1
    return [{"label": c, "count": n_} for c, n_ in counter.most_common(n)]


def summarize(rows):
    return {
        "posting_count": len(rows),
        "top_skills": top_skills(rows),
        "education": education_breakdown(rows),
        "top_categories": top_categories(rows),
    }


def main():
    rows = load_rows(INPUT_XLSX)
    now = datetime.now(timezone.utc)

    for r in rows:
        r["_scraped_dt"] = parse_scraped(r.get("date_scraped"))

    windows_out = {}
    for name, days in WINDOWS.items():
        cutoff = now - timedelta(days=days)
        subset = [r for r in rows if r["_scraped_dt"] and r["_scraped_dt"] >= cutoff]
        windows_out[name] = summarize(subset)

    windows_out["all_time"] = summarize(rows)

    payload = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "EthioJobs",
        "windows": windows_out,
    }

    out_path = Path(OUTPUT_JSON)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out_path} — {len(rows)} total rows summarized across {len(WINDOWS) + 1} windows.")


if __name__ == "__main__":
    main()
