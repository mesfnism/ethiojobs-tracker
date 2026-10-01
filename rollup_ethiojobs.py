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

Skills, job titles, education levels and sectors are classified against
standard international taxonomies (ESCO, ISCO-08, ISCED 2011, ISIC Rev.4)
rather than raw site text, via taxonomy.py — see that file for what each
scheme is and how the matching works.

Reads from combined_tracker.xlsx (built by combine_trackers.py, which
merges ethiojobs_tracker.xlsx and hahujobs_tracker.xlsx and dedups any
vacancy cross-posted to both sites) rather than a single site's tracker,
so every chart reflects vacancies from all sources at once. Run this
AFTER both site pipelines and combine_trackers.py in the same workflow,
so it always summarizes the freshly updated combined dataset.
"""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import openpyxl

import taxonomy

INPUT_XLSX = "combined_tracker.xlsx"
OUTPUT_JSON = "docs/data/rollups.json"

WINDOWS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}

CATEGORY_EXPANSIONS = {
    "Social Sciences and Com": "Social Sciences and Communications",
}

_BAD_LOCATION_LABELS = {"not specified", "n/a", "none", "tbd", "various"}


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
    """Individual skills, ESCO-normalized (merges spacing/casing variants
    like "Teamwork" / "Team work" into one count)."""
    counter = Counter()
    for r in rows:
        if not r.get("skills_required"):
            continue
        for s in r["skills_required"].split(";"):
            canon = taxonomy.normalize_skill(s)
            if canon:
                counter[canon] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def skill_groups(rows, n=8):
    """Skills rolled up to their ESCO top-level pillar (the "general skill"
    each individual skill belongs to)."""
    counter = Counter()
    for r in rows:
        if not r.get("skills_required"):
            continue
        for s in r["skills_required"].split(";"):
            canon = taxonomy.normalize_skill(s)
            if not canon:
                continue
            group = taxonomy.esco_group_for_skill(canon)
            if group != "Not classified":
                counter[group] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def education_breakdown(rows):
    """Education requirements mapped onto ISCED 2011 levels."""
    counter = Counter(
        r.get("education_required")
        for r in rows
        if r.get("education_required") in taxonomy.ISCED_LEVELS
    )
    known_total = sum(counter.values())
    levels = []
    for raw in taxonomy.ISCED_ORDER:
        c = counter.get(raw, 0)
        if c > 0:
            level, label = taxonomy.ISCED_LEVELS[raw]
            levels.append({"label": raw, "count": c, "isced_level": level, "isced_label": label})
    return {"known_total": known_total, "total": len(rows), "levels": levels}


def top_categories(rows, n=10):
    """Sectors via ISIC Rev.4, classified from the posting's own category
    tag text (falls back to nothing counted if no rule matches, rather
    than showing the site's own truncated label)."""
    counter = Counter()
    for r in rows:
        if not r.get("category"):
            continue
        for c in r["category"].split(";"):
            c = c.strip().rstrip(".")
            if not c:
                continue
            c = CATEGORY_EXPANSIONS.get(c, c)
            sector = taxonomy.isic_section_for_text(c)
            if sector:
                counter[sector["label"]] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def top_jobs(rows, n=10):
    """Occupations via ISCO-08 major groups, classified from job_title."""
    counter = Counter()
    for r in rows:
        title = r.get("job_title")
        if not title:
            continue
        group = taxonomy.isco_group_for_title(title)
        if group != "Not classified":
            counter[group] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def top_locations(rows, n=10):
    """Postings by location. Location is a clean structured field on
    EthioJobs already, so this only trims whitespace/casing — no external
    taxonomy needed."""
    counter = Counter()
    for r in rows:
        loc = r.get("location")
        if not loc:
            continue
        loc = loc.strip()
        if not loc or loc.lower() in _BAD_LOCATION_LABELS:
            continue
        counter[loc] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def source_breakdown(rows):
    """How many tracked vacancies came from each site (a cross-posted,
    merged vacancy counts under its combined source label, e.g.
    "EthioJobs, HaHuJobs", rather than being split between the two)."""
    counter = Counter(r.get("source") for r in rows if r.get("source"))
    return [{"label": label, "count": c} for label, c in counter.most_common()]


def summarize(rows):
    return {
        "posting_count": len(rows),
        "top_skills": top_skills(rows),
        "skill_groups": skill_groups(rows),
        "education": education_breakdown(rows),
        "top_categories": top_categories(rows),
        "top_jobs": top_jobs(rows),
        "top_locations": top_locations(rows),
        "source_breakdown": source_breakdown(rows),
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
        "source": "EthioJobs + HaHuJobs",
        "taxonomies": {
            "skills": "ESCO (EU skills/competences taxonomy)",
            "jobs": "ISCO-08 (ILO International Standard Classification of Occupations)",
            "education": "ISCED 2011 (UNESCO International Standard Classification of Education)",
            "sectors": "ISIC Rev.4 (UN International Standard Industrial Classification)",
        },
        "windows": windows_out,
    }

    out_path = Path(OUTPUT_JSON)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out_path} — {len(rows)} total rows summarized across {len(WINDOWS) + 1} windows.")


if __name__ == "__main__":
    main()
