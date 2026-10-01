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
    like "Teamwork" / "Team work" into one count) and filtered to reject
    sentence fragments or comma-joined category lists mistakenly tagged
    as a skill (taxonomy.normalize_skill)."""
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
    EthioJobs already; this trims whitespace and applies consistent
    title-casing (site text mixes ALL CAPS and lowercase) — no external
    taxonomy needed."""
    counter = Counter()
    for r in rows:
        loc = r.get("location")
        if not loc:
            continue
        loc = loc.strip()
        if not loc or loc.lower() in _BAD_LOCATION_LABELS:
            continue
        counter[taxonomy.title_case_label(loc)] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def sector_subcategories(rows, n=12):
    """A second, finer-grained hierarchy level beneath the ISIC section:
    the posting's own raw category/sub-sector tag text (cleaned and
    consistently cased), rather than the broad ISIC section alone. E.g.
    the ISIC section "Professional, scientific and technical activities"
    might break down into "Economics", "Project Management", "Business
    Administration and Management", etc."""
    counter = Counter()
    for r in rows:
        if not r.get("category"):
            continue
        for c in r["category"].split(";"):
            label = taxonomy.normalize_category_label(c)
            if label:
                counter[label] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


CRITICAL_SKILLS_TOP_N = 3
CRITICAL_SKILLS_MIN_POSTINGS = 3


def _top_skills_for_rows(rows, top_n):
    counter = Counter()
    for r in rows:
        if not r.get("skills_required"):
            continue
        for s in r["skills_required"].split(";"):
            canon = taxonomy.normalize_skill(s)
            if canon:
                counter[canon] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(top_n)]


def _group_rows_by(rows, group_fn):
    """Shared grouping helper for every critical_skills_by_* function below.
    `group_fn(row)` returns a single label, a list of labels (for a row
    that belongs to more than one group, e.g. multi-tagged categories), or
    something falsy to leave the row out entirely."""
    groups = {}
    for r in rows:
        labels = group_fn(r)
        if not labels:
            continue
        if isinstance(labels, str):
            labels = [labels]
        for label in labels:
            if not label:
                continue
            groups.setdefault(label, []).append(r)
    return groups


def _critical_skills_result(groups, n, min_postings):
    """Shared "gate + summarize" step for every critical_skills_by_*
    function: a group is only included once at least `min_postings` of
    its postings HAVE a skills_required value at all, so a one-off
    posting's skill list never gets reported as "the" critical skills for
    an entire group (honesty over coverage, same principle as every other
    breakdown in this file)."""
    result = {}
    for label, group_rows in groups.items():
        with_skills = [r for r in group_rows if r.get("skills_required")]
        if len(with_skills) < min_postings:
            continue
        top = _top_skills_for_rows(with_skills, n)
        if top:
            result[label] = {"postings_considered": len(with_skills), "skills": top}
    return result


def critical_skills_by_subsector(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """For each sub-sector (same grouping as sector_subcategories), the
    1-3 skills most frequently requested in postings tagged with that
    sub-sector — answers "what does a Finance posting actually ask for?"
    rather than just "how many Finance postings are there?". A posting
    tagged with more than one sub-sector (category is a ";"-joined list)
    contributes to each of its sub-sectors' tallies, same as
    sector_subcategories itself."""
    def group_fn(r):
        if not r.get("category"):
            return None
        return list({taxonomy.normalize_category_label(c) for c in r["category"].split(";")})
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_role(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea as critical_skills_by_subsector, grouped by ISCO-08
    occupation group (same grouping as top_jobs, classified from
    job_title) instead of sector — answers "what does a posting for this
    kind of role actually ask for?"."""
    def group_fn(r):
        title = r.get("job_title")
        if not title:
            return None
        group = taxonomy.isco_group_for_title(title)
        return group if group != "Not classified" else None
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_employer(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea, grouped by individual employer (same grouping as
    top_employers) — "what does THIS employer ask for across its
    postings?"."""
    def group_fn(r):
        employer = r.get("employer")
        return taxonomy.title_case_label(employer.strip()) if employer else None
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_employer_type(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea, grouped by employer type (Private / Public / NGO, same
    classification as employer_type_breakdown)."""
    def group_fn(r):
        employer = r.get("employer")
        return taxonomy.employer_type(employer) if employer else None
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_location(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea, grouped by location (same grouping as top_locations)."""
    def group_fn(r):
        loc = r.get("location")
        if not loc:
            return None
        loc = loc.strip()
        if not loc or loc.lower() in _BAD_LOCATION_LABELS:
            return None
        return taxonomy.title_case_label(loc)
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_salary_bucket(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea, grouped by salary bucket (same buckets as
    salary_breakdown) — "what do higher-paying postings actually ask
    for, versus lower-paying ones?"."""
    def group_fn(r):
        return taxonomy.salary_bucket(r.get("salary_hint"))
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_sector(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea, grouped by top-level ISIC sector (same grouping as
    top_categories) — distinct from critical_skills_by_subsector's
    finer-grained category tags."""
    def group_fn(r):
        if not r.get("category"):
            return None
        labels = []
        for c in r["category"].split(";"):
            c = c.strip().rstrip(".")
            if not c:
                continue
            c = CATEGORY_EXPANSIONS.get(c, c)
            sector = taxonomy.isic_section_for_text(c)
            if sector:
                labels.append(sector["label"])
        return labels
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_experience(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea, grouped by years-of-experience bucket (same buckets as
    experience_breakdown)."""
    def group_fn(r):
        return taxonomy.experience_bucket(r.get("years_experience_required"))
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def critical_skills_by_education(rows, n=CRITICAL_SKILLS_TOP_N, min_postings=CRITICAL_SKILLS_MIN_POSTINGS):
    """Same idea, grouped by ISCED education level (same grouping as
    education_breakdown)."""
    def group_fn(r):
        level = r.get("education_required")
        return level if level in taxonomy.ISCED_LEVELS else None
    return _critical_skills_result(_group_rows_by(rows, group_fn), n, min_postings)


def experience_breakdown(rows):
    """Years-of-experience required, bucketed into standard ranges."""
    counter = Counter()
    known_total = 0
    for r in rows:
        bucket = taxonomy.experience_bucket(r.get("years_experience_required"))
        if bucket:
            counter[bucket] += 1
            known_total += 1
    buckets = []
    for label in taxonomy.EXPERIENCE_BUCKET_ORDER:
        c = counter.get(label, 0)
        if c > 0:
            buckets.append({"label": label, "count": c})
    return {"known_total": known_total, "total": len(rows), "buckets": buckets}


def education_specializations(rows, n=8):
    """For each ISCED education level, the fields of study mentioned in
    the posting's free-text description, where one exists (mainly
    HaHuJobs postings — EthioJobs never stored free-text descriptions, so
    its postings show up only in "known_total" / "total", not in any
    field). This powers the dashboard's "<level> in what?" drill-down."""
    by_level = {}
    for raw_level in taxonomy.ISCED_ORDER:
        rows_at_level = [r for r in rows if r.get("education_required") == raw_level]
        counter = Counter()
        with_field = 0
        for r in rows_at_level:
            desc = r.get("description") or r.get("job_description")
            fields = taxonomy.extract_specializations(desc) if desc else []
            if fields:
                with_field += 1
            for f in fields:
                counter[f] += 1
        if rows_at_level:
            by_level[raw_level] = {
                "total": len(rows_at_level),
                "with_field_stated": with_field,
                "fields": [{"label": label, "count": c} for label, c in counter.most_common(n)],
            }
    return by_level


def top_employers(rows, n=10):
    """Top individual employers/institutions by posting count. Site text
    casing for employer names is mixed (ALL CAPS vs Title Case), so this
    normalizes casing for display but keeps the un-normalized spacing
    (two differently-punctuated spellings of the same company are not
    merged here — that's what combine_trackers.py's dedup already does
    for genuinely duplicate postings)."""
    counter = Counter()
    for r in rows:
        employer = r.get("employer")
        if not employer:
            continue
        counter[taxonomy.title_case_label(employer.strip())] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def employer_type_breakdown(rows):
    """Postings aggregated by employer type: Private / Public / NGO,
    classified from the employer's own name via a keyword heuristic
    (taxonomy.employer_type) rather than a verified registry lookup."""
    counter = Counter()
    for r in rows:
        employer = r.get("employer")
        if not employer:
            continue
        etype = taxonomy.employer_type(employer)
        if etype:
            counter[etype] += 1
    known_total = counter.get("Private", 0) + counter.get("Public", 0) + counter.get("NGO", 0)
    order = ["Private", "Public", "NGO", "Unclassified"]
    return {
        "known_total": known_total,
        "total": len(rows),
        "types": [{"label": label, "count": counter[label]} for label in order if counter.get(label)],
    }


def salary_breakdown(rows):
    """Pay, bucketed into standard monthly-ETB ranges, for postings that
    state a figure clearly enough (taxonomy.salary_bucket) — "Negotiable"
    or "As per company scale" are left out of known_total rather than
    guessed at."""
    counter = Counter()
    known_total = 0
    for r in rows:
        bucket = taxonomy.salary_bucket(r.get("salary_hint"))
        if bucket:
            counter[bucket] += 1
            known_total += 1
    buckets = []
    for label in taxonomy.SALARY_BUCKET_ORDER:
        c = counter.get(label, 0)
        if c > 0:
            buckets.append({"label": label, "count": c})
    return {"known_total": known_total, "total": len(rows), "buckets": buckets}


def source_breakdown(rows):
    """How many tracked vacancies came from each site (a cross-posted,
    merged vacancy counts under its combined source label, e.g.
    "EthioJobs, HaHuJobs", rather than being split between the two)."""
    counter = Counter(r.get("source") for r in rows if r.get("source"))
    return [{"label": label, "count": c} for label, c in counter.most_common()]


def source_site_breakdown(rows, n=10):
    """Ranks individual sites by how many postings they contributed. A
    vacancy cross-posted to more than one site (merged by
    combine_trackers.py into one row, with source="EthioJobs, HaHuJobs")
    counts toward EACH of those sites here — this answers "how many jobs
    did each site publish", not "how many final unique vacancies", which
    is what source_breakdown (above) answers instead."""
    counter = Counter()
    for r in rows:
        if not r.get("source"):
            continue
        for site in r["source"].split(","):
            site = site.strip()
            if site:
                counter[site] += 1
    return [{"label": label, "count": c} for label, c in counter.most_common(n)]


def posting_trends(rows):
    """Postings tracked over time, bucketed by the calendar week, month,
    and year each posting was FIRST SEEN (date_scraped) — not a rolling
    window like the rest of this file, but every bucket a posting has
    ever landed in, so a trend line can show "new postings per month"
    accumulating across the whole history tracked so far, however long
    that is. Sorted oldest to newest; empty buckets between the first
    and last aren't invented (a gap in the data stays a gap)."""
    daily, weekly, monthly, yearly = Counter(), Counter(), Counter(), Counter()
    for r in rows:
        dt = r.get("_scraped_dt")
        if not dt:
            continue
        iso_year, iso_week, _ = dt.isocalendar()
        daily[dt.strftime("%Y-%m-%d")] += 1
        weekly[f"{iso_year}-W{iso_week:02d}"] += 1
        monthly[dt.strftime("%Y-%m")] += 1
        yearly[dt.strftime("%Y")] += 1

    def to_series(counter):
        return [{"period": k, "count": v} for k, v in sorted(counter.items())]

    return {
        "daily": to_series(daily),
        "weekly": to_series(weekly),
        "monthly": to_series(monthly),
        "yearly": to_series(yearly),
    }


def summarize(rows):
    return {
        "posting_count": len(rows),
        "top_skills": top_skills(rows),
        "skill_groups": skill_groups(rows),
        "education": education_breakdown(rows),
        "education_specializations": education_specializations(rows),
        "top_categories": top_categories(rows),
        "sector_subcategories": sector_subcategories(rows),
        "top_jobs": top_jobs(rows),
        "top_locations": top_locations(rows),
        "experience": experience_breakdown(rows),
        "salary": salary_breakdown(rows),
        "top_employers": top_employers(rows),
        "employer_type": employer_type_breakdown(rows),
        "source_breakdown": source_breakdown(rows),
        "source_sites": source_site_breakdown(rows),
        "critical_skills_by_subsector": critical_skills_by_subsector(rows),
        "critical_skills_by_role": critical_skills_by_role(rows),
        "critical_skills_by_employer": critical_skills_by_employer(rows),
        "critical_skills_by_employer_type": critical_skills_by_employer_type(rows),
        "critical_skills_by_location": critical_skills_by_location(rows),
        "critical_skills_by_salary_bucket": critical_skills_by_salary_bucket(rows),
        "critical_skills_by_sector": critical_skills_by_sector(rows),
        "critical_skills_by_experience": critical_skills_by_experience(rows),
        "critical_skills_by_education": critical_skills_by_education(rows),
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
    trends = posting_trends(rows)

    payload = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "EthioJobs + HaHuJobs",
        "taxonomies": {
            "skills": "ESCO (EU skills/competences taxonomy)",
            "jobs": "ISCO-08 (ILO International Standard Classification of Occupations)",
            "education": "ISCED 2011 (UNESCO International Standard Classification of Education)",
            "sectors": "ISIC Rev.4 (UN International Standard Industrial Classification)",
        },
        "notes": {
            "education_specializations": "Field-of-study is extracted from free-text "
                "descriptions, which only HaHuJobs postings have — EthioJobs postings "
                "contribute to each level's total but rarely to a specific field.",
            "employer_type": "Private / Public / NGO is a keyword heuristic on the "
                "employer's own name, not a verified registry lookup.",
            "critical_skills": f"A sub-sector or role only appears here once at least "
                f"{CRITICAL_SKILLS_MIN_POSTINGS} of its postings state a skills_required "
                f"value, so a top-{CRITICAL_SKILLS_TOP_N} list is never built from a single posting.",
        },
        "windows": windows_out,
        "trends": trends,
    }

    out_path = Path(OUTPUT_JSON)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out_path} — {len(rows)} total rows summarized across {len(WINDOWS) + 1} windows.")


if __name__ == "__main__":
    main()
