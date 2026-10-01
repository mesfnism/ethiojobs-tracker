"""
Combine Trackers — merges ethiojobs_tracker.xlsx and hahujobs_tracker.xlsx
into one combined_tracker.xlsx, so the dashboard reflects vacancies from
every source in one place, the way the user asked: "the final results,
the vacancies, aggregated."

Cross-source duplicate detection (the same real vacancy posted on both
EthioJobs and HaHuJobs) matches on:
  - employer name, normalized (lowercased, legal suffixes like "PLC",
    "S.C.", "Ltd" stripped, punctuation/whitespace collapsed)
  - job title, normalized the same way (plus stripping "(Re-Advertised)"
    style suffixes)
  - location, normalized (lowercased, trimmed)
  - AND first-seen dates (date_scraped) within DEDUP_WINDOW_DAYS of each
    other

That last condition matters: a company that reposts "Accountant" every
few months is posting genuinely separate vacancies, not the same one
twice — only two postings that are close together in time, from
different sites, with the same employer/title/location, are treated as
one vacancy cross-posted to both boards. Matches are never made between
two postings from the SAME source — each site's own scraper already
dedups by job_id, so anything left in one tracker is already a distinct
posting on that site.

A merged row keeps the richer set of fields (whichever posting on either
site supplied a value, for every column), notes both source names in
`source`, and keeps both postings' URLs so nothing is lost.
"""

import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill

INPUT_FILES = ["ethiojobs_tracker.xlsx", "hahujobs_tracker.xlsx", "reporterjobs_tracker.xlsx"]
OUTPUT_XLSX = "combined_tracker.xlsx"
DEDUP_WINDOW_DAYS = 14

COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "other_source_url", "description",
]

_LEGAL_SUFFIXES = re.compile(
    r"\b(plc|p\.l\.c\.?|s\.c\.?|share company|ltd|llc|inc|co\.?|company|"
    r"pvt\.?\s*ltd\.?)\b\.?",
    re.I,
)
_TITLE_NOISE = re.compile(
    r"\s*[\(\-–]\s*(re[\s\-]?advertised|readvertised|new|urgent)\s*[\)]?\s*$",
    re.I,
)
_PUNCT_WS = re.compile(r"[^\w\s]")
_MULTI_WS = re.compile(r"\s+")


def normalize_employer(name):
    if not name:
        return ""
    s = name.lower()
    s = _LEGAL_SUFFIXES.sub("", s)
    s = _PUNCT_WS.sub(" ", s)
    return _MULTI_WS.sub(" ", s).strip()


def normalize_title(title):
    if not title:
        return ""
    s = _TITLE_NOISE.sub("", title)
    s = s.lower()
    s = _PUNCT_WS.sub(" ", s)
    return _MULTI_WS.sub(" ", s).strip()


def normalize_location(loc):
    if not loc:
        return ""
    return _MULTI_WS.sub(" ", loc.lower().strip())


def parse_scraped(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def load_rows(path):
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except FileNotFoundError:
        print(f"  [info] {path} not found yet — skipping (0 rows)", file=sys.stderr)
        return []
    ws = wb["Jobs"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    rows = [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True)]
    wb.close()
    return rows


def merge_pair(a, b):
    """Merge two duplicate rows: prefer non-None values, note both
    sources and both URLs."""
    merged = {}
    for col in COLUMNS:
        if col in ("source", "other_source_url"):
            continue
        va, vb = a.get(col), b.get(col)
        merged[col] = va if va not in (None, "") else vb

    sources = sorted({a.get("source"), b.get("source")} - {None})
    merged["source"] = ", ".join(sources)

    urls = [u for u in (a.get("source_url"), b.get("source_url")) if u]
    merged["source_url"] = urls[0] if urls else None
    merged["other_source_url"] = urls[1] if len(urls) > 1 else None
    return merged


def combine(all_rows):
    buckets = defaultdict(list)
    for i, r in enumerate(all_rows):
        key = (
            normalize_employer(r.get("employer")),
            normalize_title(r.get("job_title")),
            normalize_location(r.get("location")),
        )
        buckets[key].append(i)

    merged_into = {}  # index -> merged row, for indices consumed by a merge
    consumed = set()
    dup_count = 0

    for key, idxs in buckets.items():
        if len(idxs) < 2 or not any(key):
            continue
        # Only compare pairs from different sources, within the date window
        for a_i in range(len(idxs)):
            i = idxs[a_i]
            if i in consumed:
                continue
            for b_i in range(a_i + 1, len(idxs)):
                j = idxs[b_i]
                if j in consumed:
                    continue
                row_i, row_j = all_rows[i], all_rows[j]
                if row_i.get("source") == row_j.get("source"):
                    continue
                d_i, d_j = parse_scraped(row_i.get("date_scraped")), parse_scraped(row_j.get("date_scraped"))
                if d_i and d_j and abs((d_i - d_j).days) > DEDUP_WINDOW_DAYS:
                    continue
                merged = merge_pair(row_i, row_j)
                merged_into[i] = merged
                consumed.add(i)
                consumed.add(j)
                dup_count += 1
                break  # i is consumed, move to next i

    combined_rows = []
    for i, r in enumerate(all_rows):
        if i in merged_into:
            combined_rows.append(merged_into[i])
        elif i not in consumed:
            row = {col: r.get(col) for col in COLUMNS}
            combined_rows.append(row)
    return combined_rows, dup_count


def write_output(rows, per_source_counts, dup_count):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3B57")
    for r in rows:
        ws.append([r.get(col) for col in COLUMNS])

    log = wb.create_sheet("Run Log")
    log.append(["run_timestamp_utc", "ethiojobs_rows", "hahujobs_rows",
                "duplicates_merged", "combined_total"])
    for cell in log[1]:
        cell.font = Font(bold=True)
    log.append([
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        per_source_counts.get("EthioJobs", 0),
        per_source_counts.get("HaHuJobs", 0),
        dup_count,
        len(rows),
    ])
    wb.save(OUTPUT_XLSX)


def main():
    all_rows = []
    per_source_counts = {}
    for path in INPUT_FILES:
        rows = load_rows(path)
        for r in rows:
            per_source_counts[r.get("source")] = per_source_counts.get(r.get("source"), 0) + 1
        all_rows.extend(rows)

    combined_rows, dup_count = combine(all_rows)
    write_output(combined_rows, per_source_counts, dup_count)
    print(f"Combined {len(all_rows)} source rows -> {len(combined_rows)} vacancies "
          f"({dup_count} cross-source duplicates merged) in {OUTPUT_XLSX}.")


if __name__ == "__main__":
    main()
