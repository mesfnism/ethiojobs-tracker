"""
Palm Jobs Pipeline — fourth job board (palmjobs.et), a Next.js app backed
by a public Supabase Postgres table. Unlike every other site in this
project, this one is NOT scraped by parsing rendered text: its `jobs`
table is readable anonymously (confirmed while signed out of any account —
browsing it never touches personal/candidate data), and openly serves
clean structured fields most other sites only state in free text:
min/max salary with currency (ETB or USD), education level, field of
study, required skills (as an actual list), experience level, and more.

How this gets the data, and why: loading the /jobs page triggers the
site's own front end to fetch ONE job's detail from
`<project>.supabase.co/rest/v1/jobs` — the exact same public endpoint
used for every job. This pipeline opens that page, captures the request
headers the site's own code used for that one call (the public anon key,
same as any visitor's browser sends), and reuses those headers to page
through the SAME table itself, in the SAME shape, via Playwright's
request context — not a different, non-public API, not a different
request shape, not a bypassed protection of any kind. It never touches
account-scoped tables (candidates, resumes, notifications, user_*) —
only the public `jobs` and `companies_public` tables the site already
shows every anonymous visitor.

Job IDs come directly from the API's own `id` field (a real UUID), so
dedup across runs is exact — no derived/hashed identifier needed, unlike
sites where only scraped text is available.
"""

import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

LIST_PAGE_URL = "https://palmjobs.et/jobs"
SUPABASE_JOBS_PATH_MARKER = "/rest/v1/jobs"

PAGE_SIZE = int(os.environ.get("PALMJOBS_PAGE_SIZE", "50"))
MAX_PAGES = int(os.environ.get("PALMJOBS_MAX_PAGES", "40"))
STOP_AFTER_CONSECUTIVE_SEEN = int(os.environ.get("PALMJOBS_STOP_AFTER_SEEN", "25"))
REQUEST_DELAY_SECONDS = float(os.environ.get("PALMJOBS_REQUEST_DELAY", "1.0"))

OUTPUT_XLSX = os.environ.get("PALMJOBS_OUTPUT_XLSX", "palmjobs_tracker.xlsx")

SELECT_FIELDS = (
    "id,employer_id,company_id,company_name,job_title,job_location,"
    "open_positions,job_type,experience_level,min_salary,max_salary,"
    "currency,job_description,application_deadline,external_link,"
    "job_status,date_posted,created_at,job_industry,education_level,"
    "field_of_study,required_skills,is_aggregated,source_url,source_name,"
    "email_application"
)

COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

NAVY = "1F3B57"

_EDUCATION_NORMALIZE = [
    (re.compile(r"\bph\.?d\b|\bdoctorate\b|\bdoctoral\b", re.I), "PhD"),
    (re.compile(r"\bmaster'?s?\b", re.I), "Master's degree"),
    (re.compile(r"\bbachelor'?s?\b|\bundergraduate\b", re.I), "Bachelor's degree"),
    (re.compile(r"\bdiploma\b", re.I), "Diploma"),
    (re.compile(r"\bcertificate\b|\btvet\b", re.I), "Certificate"),
]


def normalize_education(raw):
    if not raw:
        return None
    for pattern, label in _EDUCATION_NORMALIZE:
        if pattern.search(raw):
            return label
    return None  # left unclassified rather than guessed — matches ISCED_LEVELS keys only


def build_salary_hint(row):
    """Builds a text salary_hint from the API's structured min/max/currency
    fields, in the same shape taxonomy.salary_bucket already parses
    ("<currency> <amount>" or "<currency> <lo> - <hi>"), so the existing
    salary chart picks these up with no special-casing — except that
    taxonomy.salary_bucket only buckets Birr/ETB figures; a USD salary
    here is preserved as text but won't be bucketed (honest gap, not a
    silent currency conversion)."""
    currency = (row.get("currency") or "").strip()
    lo, hi = row.get("min_salary"), row.get("max_salary")
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo != hi:
        return f"{currency} {lo} - {hi}".strip()
    amount = lo if lo is not None else hi
    return f"{currency} {amount}".strip()


def structure_job(row):
    skills = row.get("required_skills")
    if isinstance(skills, list):
        skills_required = "; ".join(str(s) for s in skills if s)
    else:
        skills_required = skills

    return {
        "job_id": row.get("id"),
        "job_title": row.get("job_title"),
        "employer": row.get("company_name"),
        "category": row.get("job_industry"),
        "location": row.get("job_location"),
        "work_type": None,
        "career_level": None,
        "employment_type": row.get("job_type"),
        "number_required": row.get("open_positions"),
        "education_required": normalize_education(row.get("education_level")),
        "years_experience_required": row.get("experience_level"),
        "skills_required": skills_required or None,
        "special_skill_training": None,
        "salary_hint": build_salary_hint(row),
        "application_deadline": row.get("application_deadline"),
        "how_to_apply": row.get("external_link") or row.get("email_application"),
        "source_url": f"https://palmjobs.et/jobs?jobId={row.get('id')}",
        "source": "PalmJobs",
        "date_posted_relative": row.get("date_posted"),
        "date_scraped": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "extraction_method": "public_api",
        # job_description doubles as the free-text source for education
        # specialization extraction (field_of_study is also returned
        # directly by the API and could be used as-is in a future pass).
        "description": row.get("job_description") or row.get("field_of_study"),
    }


# --------------------------------------------------------------------------
# Browser-driven fetch: capture real request headers, then page the table
# --------------------------------------------------------------------------

def capture_api_headers(page):
    """Loads the /jobs page and captures the headers the site's own code
    sends on its first call to the public jobs table — the same headers
    any anonymous visitor's browser would send. Returns None if no such
    call is seen within the timeout (e.g. the page's own behavior
    changed), so the caller can fail loudly rather than guess at auth."""
    captured = {}

    def on_request(request):
        if SUPABASE_JOBS_PATH_MARKER in request.url and "headers" not in captured:
            captured["headers"] = dict(request.headers)
            captured["base_url"] = request.url.split("/rest/v1/jobs")[0] + "/rest/v1/jobs"

    page.on("request", on_request)
    page.goto(LIST_PAGE_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    page.remove_listener("request", on_request)
    return captured.get("headers"), captured.get("base_url")


def fetch_jobs_page(request_context, base_url, headers, offset):
    url = (
        f"{base_url}?select={SELECT_FIELDS}&job_status=eq.Active"
        f"&order=created_at.desc&limit={PAGE_SIZE}&offset={offset}"
    )
    resp = request_context.get(url, headers=headers, timeout=30000)
    if resp.status != 200:
        raise RuntimeError(f"Unexpected status {resp.status} fetching jobs page: {resp.text()[:300]}")
    return resp.json()


# --------------------------------------------------------------------------
# Excel read/write — identical pattern to the other pipelines
# --------------------------------------------------------------------------

def load_existing_ids(path: Path):
    if not path.exists():
        return set()
    wb = openpyxl.load_workbook(path, read_only=True)
    if "Jobs" not in wb.sheetnames:
        return set()
    ws = wb["Jobs"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    id_col = header.index("job_id")
    ids = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[id_col]:
            ids.add(str(row[id_col]))
    wb.close()
    return ids


def _style_header(ws, ncols):
    for col_idx in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}1"


def append_rows(path: Path, rows):
    if path.exists():
        wb = openpyxl.load_workbook(path)
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

    if "Jobs" not in wb.sheetnames:
        ws = wb.create_sheet("Jobs")
        ws.append(COLUMNS)
        _style_header(ws, len(COLUMNS))
    else:
        ws = wb["Jobs"]

    for row in rows:
        ws.append([row.get(c) for c in COLUMNS])

    if "Run Log" not in wb.sheetnames:
        log = wb.create_sheet("Run Log")
        log.append(["run_timestamp_utc", "new_rows_added", "total_rows_after"])
        _style_header(log, 3)
    else:
        log = wb["Run Log"]

    total_rows_after = ws.max_row - 1
    log.append([
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        len(rows),
        total_rows_after,
    ])
    wb.save(path)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run: pip install -r requirements.txt && playwright install chromium")
        sys.exit(1)

    output_path = Path(OUTPUT_XLSX)
    existing_ids = load_existing_ids(output_path)
    print(f"Loaded {len(existing_ids)} existing job IDs from {output_path}")

    new_rows = []
    consecutive_seen = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        headers, base_url = capture_api_headers(page)
        if not headers or not base_url:
            print("  Could not capture API request headers from the live page — "
                  "the site's behavior may have changed. Stopping without writing "
                  "anything, rather than guessing at credentials.")
            page.close()
            browser.close()
            append_rows(output_path, [])
            return

        request_context = page.context.request
        page.close()

        for page_num in range(MAX_PAGES):
            offset = page_num * PAGE_SIZE
            try:
                batch = fetch_jobs_page(request_context, base_url, headers, offset)
            except Exception as e:
                print(f"  Offset {offset}: fetch failed ({e}) — stopping pagination.")
                break

            if not batch:
                print(f"  Offset {offset}: no more jobs — reached the end.")
                break

            page_new = 0
            for row in batch:
                job_id = row.get("id")
                if job_id in existing_ids:
                    consecutive_seen += 1
                else:
                    consecutive_seen = 0
                    new_rows.append(structure_job(row))
                    existing_ids.add(job_id)
                    page_new += 1

            print(f"  Offset {offset}: {len(batch)} jobs, {page_new} new, "
                  f"{consecutive_seen} consecutive already-seen.")

            if consecutive_seen >= STOP_AFTER_CONSECUTIVE_SEEN:
                print(f"  Hit {STOP_AFTER_CONSECUTIVE_SEEN} consecutive already-seen postings — "
                      f"caught up, stopping pagination.")
                break
            if len(batch) < PAGE_SIZE:
                break
            time.sleep(REQUEST_DELAY_SECONDS)

        browser.close()

    if new_rows:
        append_rows(output_path, new_rows)
        print(f"Added {len(new_rows)} new postings. Workbook: {output_path}")
    else:
        append_rows(output_path, [])
        print("No new postings this run.")


if __name__ == "__main__":
    main()
