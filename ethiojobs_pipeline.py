"""
EthioJobs Daily Pipeline — Phase 1 (private-sector slice)

Companion to the ReliefWeb pipeline (Phase 0). ReliefWeb covers the
donor/NGO/UN-system slice of the Ethiopian/Horn-of-Africa labour market;
this script covers EthioJobs, Ethiopia's largest private-sector job board.

EthioJobs renders its listings with client-side JavaScript (confirmed by
testing: a plain HTTP fetch returns an empty loading shell), so this script
uses Playwright to render the page like a real browser before reading it —
that's the whole reason it's a separate, heavier script than the ReliefWeb
one, which just calls a JSON API.

Design mirrors the ReliefWeb pipeline exactly:
  fetch (rendered listing pages) -> structure (pure parsing functions,
  testable without a browser) -> dedupe (persisted job-id set from the
  existing output file) -> append-only Excel write, with a Run Log tab.

Because the listing is sorted newest-first by default, a daily run does not
need to re-scan all ~1,000+ live postings: it walks pages from page 1 and
stops once it hits a run of postings it has already seen (see
STOP_AFTER_CONSECUTIVE_SEEN below), which keeps daily runs fast and polite.
"""

import os
import re
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BASE_URL = "https://ethiojobs.net"
LIST_URL_TEMPLATE = BASE_URL + "/jobs?page={page}"

MAX_PAGES = int(os.environ.get("ETHIOJOBS_MAX_PAGES", "100"))          # hard safety cap
STOP_AFTER_CONSECUTIVE_SEEN = int(os.environ.get("ETHIOJOBS_STOP_AFTER_SEEN", "15"))
LIST_REQUEST_DELAY_SECONDS = float(os.environ.get("ETHIOJOBS_LIST_DELAY", "1.5"))
DETAIL_REQUEST_DELAY_SECONDS = float(os.environ.get("ETHIOJOBS_DETAIL_DELAY", "1.5"))
MAX_NEW_DETAIL_FETCHES_PER_RUN = int(os.environ.get("ETHIOJOBS_MAX_NEW_PER_RUN", "300"))

OUTPUT_XLSX = os.environ.get("ETHIOJOBS_OUTPUT_XLSX", "ethiojobs_tracker.xlsx")

# Words that appear as badges on a listing card, never as a real category name.
BADGE_WORDS = {"New", "Pro", "Plus", "Premium", "Featured", "Easy Apply"}

# Loose salary patterns seen in EthioJobs free text (e.g. "Monthly Salary: ETB 87,975.00")
SALARY_PATTERNS = [
    re.compile(r"(?:monthly\s+salary|salary)\s*[:\-]?\s*(ETB|USD|Birr)?\s*([\d,]+(?:\.\d+)?)", re.I),
]

EDUCATION_PATTERNS = [
    (re.compile(r"\bph\.?d\b|\bdoctorate\b", re.I), "PhD"),
    (re.compile(r"\bmaster'?s?\b|\bm\.?a\.?\b|\bm\.?sc\.?\b|\bmba\b", re.I), "Master's degree"),
    (re.compile(r"\bbachelor'?s?\b|\bb\.?a\.?\b|\bb\.?sc\.?\b|\bba\/bsc\b|\bfirst degree\b", re.I), "Bachelor's degree"),
    (re.compile(r"\bdiploma\b", re.I), "Diploma"),
    (re.compile(r"\bcertificate\b", re.I), "Certificate"),
]

COLUMNS = [
    "job_id",
    "job_title",
    "employer",
    "category",
    "location",
    "work_type",
    "career_level",
    "employment_type",
    "number_required",
    "education_required",
    "years_experience_required",
    "skills_required",
    "special_skill_training",
    "salary_hint",
    "application_deadline",
    "how_to_apply",
    "source_url",
    "source",
    "date_posted_relative",
    "date_scraped",
    "extraction_method",
]

NAVY = "1F3B57"
GOLD = "B5892B"


# --------------------------------------------------------------------------
# Pure parsing functions — no Playwright/network involved, fully unit-testable
# --------------------------------------------------------------------------

def _job_id_from_href(href: str) -> str:
    """'/job/aRgBcZ2tY9-junior-accountant' -> 'aRgBcZ2tY9'"""
    slug = href.rstrip("/").split("/")[-1]
    return slug.split("-", 1)[0]


def parse_card_text(href: str, raw_text: str) -> dict:
    """
    Parse a single listing card's innerText (Playwright: card.inner_text()),
    which comes as blocks separated by blank lines, in a fixed order:

        [badges...]
        [category tag(s), 1 or more]
        Title
        "<relative time> ago by"
        Company
        Location
        Deadline date
        Work type (Office/Hybrid/Remote)
        Description (often truncated with "...")
        "See More"
    """
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]

    # Find the "X ago by" marker — the one fixed anchor in the block.
    ago_idx = next((i for i, ln in enumerate(lines) if re.search(r"\bago by$", ln, re.I)), None)
    if ago_idx is None or ago_idx == 0:
        # Unexpected shape — return what we can, flagged for review.
        return {
            "job_id": _job_id_from_href(href),
            "job_title": lines[0] if lines else None,
            "employer": None,
            "category": None,
            "location": None,
            "work_type": None,
            "application_deadline": None,
            "date_posted_relative": None,
            "source_url": BASE_URL + href,
            "_parse_warning": "unexpected_card_shape",
        }

    title = lines[ago_idx - 1]
    posted_relative = lines[ago_idx]
    pre_title = [ln for ln in lines[:ago_idx - 1] if ln not in BADGE_WORDS]
    category = "; ".join(pre_title) if pre_title else None

    tail = lines[ago_idx + 1:]
    # tail = [Company, Location, Deadline, WorkType, Description..., "See More"]
    company = tail[0] if len(tail) > 0 else None
    location = tail[1] if len(tail) > 1 else None
    deadline = tail[2] if len(tail) > 2 else None
    work_type = tail[3] if len(tail) > 3 else None

    return {
        "job_id": _job_id_from_href(href),
        "job_title": title,
        "employer": company,
        "category": category,
        "location": location,
        "work_type": work_type,
        "application_deadline": deadline,
        "date_posted_relative": posted_relative,
        "source_url": BASE_URL + href,
    }


def parse_detail_text(raw_text: str) -> dict:
    """
    Parse a job detail page's body text (Playwright: page.inner_text('body')
    on the job content region, or the full body — labelled sections are
    unique enough to find reliably either way). Labels observed on
    ethiojobs.net detail pages:

        Location Type : <value>
        Deadline : <value>
        Career Level : <value>
        Employment Type : <value>
        Number of people required : <value>
        About the Job ... (until "About You" or "How To Apply")
        Education
        <value>
        Work Experience
        <value>
        Special skill/Training
        <value>
        Requirement Skill
        <value line 1>
        <value line 2>
        ...
        How To Apply
        <value line(s)>
    """
    text = raw_text

    def field(label):
        m = re.search(rf"{re.escape(label)}\s*:\s*(.+)", text)
        return m.group(1).strip() if m else None

    career_level = field("Career Level")
    employment_type = field("Employment Type")
    number_required = field("Number of people required")
    location_type = field("Location Type")
    deadline = field("Deadline")

    def section(label, stop_labels):
        idx = text.find(label)
        if idx == -1:
            return None
        after = text[idx + len(label):]
        stop_positions = [after.find(s) for s in stop_labels if after.find(s) != -1]
        end = min(stop_positions) if stop_positions else len(after)
        return after[:end].strip(" \n:")

    education_raw = section("Education", ["Work Experience", "Special skill", "Requirement Skill", "How To Apply"])
    experience_raw = section("Work Experience", ["Special skill", "Requirement Skill", "How To Apply"])
    special_skill = section("Special skill/Training", ["Requirement Skill", "How To Apply"])
    skills_raw = section("Requirement Skill", ["How To Apply"])
    how_to_apply = section("How To Apply", ["More Jobs by", "Search Similar Jobs", "FAQ"])

    education_required = None
    if education_raw:
        for pattern, label in EDUCATION_PATTERNS:
            if pattern.search(education_raw):
                education_required = label
                break
        if education_required is None:
            education_required = education_raw.split("\n")[0][:120]

    years_experience_required = None
    if experience_raw:
        m = re.search(r"(\d+\s*-\s*\d+|\d+\+?)\s*years?", experience_raw, re.I)
        years_experience_required = m.group(0) if m else experience_raw.split("\n")[0][:60]

    skills_required = None
    if skills_raw:
        skill_lines = [ln.strip() for ln in skills_raw.split("\n") if ln.strip()]
        skills_required = "; ".join(skill_lines[:15])

    salary_hint = None
    for pattern in SALARY_PATTERNS:
        m = pattern.search(text)
        if m:
            currency = m.group(1) or ""
            amount = m.group(2)
            salary_hint = f"{currency} {amount}".strip()
            break

    return {
        "career_level": career_level,
        "employment_type": employment_type,
        "number_required": number_required,
        "location_type": location_type,
        "application_deadline_detail": deadline,
        "education_required": education_required,
        "years_experience_required": years_experience_required,
        "special_skill_training": special_skill,
        "skills_required": skills_required,
        "how_to_apply": how_to_apply,
        "salary_hint": salary_hint,
    }


def structure_job(listing_fields: dict, detail_fields: dict | None) -> dict:
    """Merge listing-page fields with detail-page fields into the fixed COLUMNS schema."""
    detail_fields = detail_fields or {}
    row = {
        "job_id": listing_fields.get("job_id"),
        "job_title": listing_fields.get("job_title"),
        "employer": listing_fields.get("employer"),
        "category": listing_fields.get("category"),
        "location": listing_fields.get("location"),
        "work_type": listing_fields.get("work_type"),
        "career_level": detail_fields.get("career_level"),
        "employment_type": detail_fields.get("employment_type"),
        "number_required": detail_fields.get("number_required"),
        "education_required": detail_fields.get("education_required"),
        "years_experience_required": detail_fields.get("years_experience_required"),
        "skills_required": detail_fields.get("skills_required"),
        "special_skill_training": detail_fields.get("special_skill_training"),
        "salary_hint": detail_fields.get("salary_hint"),
        "application_deadline": detail_fields.get("application_deadline_detail") or listing_fields.get("application_deadline"),
        "how_to_apply": detail_fields.get("how_to_apply"),
        "source_url": listing_fields.get("source_url"),
        "source": "EthioJobs",
        "date_posted_relative": listing_fields.get("date_posted_relative"),
        "date_scraped": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "extraction_method": "dom_render" if detail_fields else "listing_only",
    }
    return row


# --------------------------------------------------------------------------
# Browser-driven fetch functions (require Playwright + real internet access —
# will NOT work inside a network-sandboxed environment; see README)
# --------------------------------------------------------------------------

def fetch_listing_page(page, page_num: int) -> list[tuple[str, str]]:
    """Returns list of (href, card_inner_text) for one listing page."""
    page.goto(LIST_URL_TEMPLATE.format(page=page_num), wait_until="networkidle", timeout=30000)
    page.wait_for_selector("div.job-card-item-container", timeout=15000)
    cards = page.query_selector_all("div.job-card-item-container")
    results = []
    for card in cards:
        link = card.query_selector('a[href^="/job/"]')
        if not link:
            continue
        href = link.get_attribute("href")
        results.append((href, card.inner_text()))
    return results


def fetch_detail_page(page, url: str) -> str:
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_selector("text=How To Apply", timeout=15000)
    return page.inner_text("body")


# --------------------------------------------------------------------------
# Excel read/write (identical pattern to the ReliefWeb pipeline)
# --------------------------------------------------------------------------

def load_existing_ids(path: Path) -> set:
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


def append_rows(path: Path, rows: list[dict]):
    if path.exists():
        wb = openpyxl.load_workbook(path)
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

    if "Jobs" not in wb.sheetnames:
        ws = wb.create_sheet("Jobs")
        ws.append(COLUMNS)
        _style_header(ws, len(COLUMNS))
        for i, col_name in enumerate(COLUMNS, start=1):
            ws.column_dimensions[get_column_letter(i)].width = max(14, min(45, len(col_name) + 4))
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

    total_rows_after = ws.max_row - 1  # minus header
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
    parser = argparse.ArgumentParser(description="EthioJobs daily pipeline")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--output", default=OUTPUT_XLSX)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run: pip install -r requirements.txt && playwright install chromium")
        sys.exit(1)

    output_path = Path(args.output)
    existing_ids = load_existing_ids(output_path)
    print(f"Loaded {len(existing_ids)} existing job IDs from {output_path}")

    new_listing_fields = []  # list of dicts, newest-first
    consecutive_seen = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        list_page = browser.new_page()

        for page_num in range(1, args.max_pages + 1):
            try:
                cards = fetch_listing_page(list_page, page_num)
            except Exception as e:
                print(f"  Page {page_num}: failed to load ({e}) — stopping pagination.")
                break

            if not cards:
                print(f"  Page {page_num}: no cards found — reached the end.")
                break

            page_new = 0
            for href, card_text in cards:
                fields = parse_card_text(href, card_text)
                if fields["job_id"] in existing_ids:
                    consecutive_seen += 1
                else:
                    consecutive_seen = 0
                    new_listing_fields.append(fields)
                    page_new += 1

            print(f"  Page {page_num}: {len(cards)} cards, {page_new} new, "
                  f"{consecutive_seen} consecutive already-seen.")

            if consecutive_seen >= STOP_AFTER_CONSECUTIVE_SEEN:
                print(f"  Hit {STOP_AFTER_CONSECUTIVE_SEEN} consecutive already-seen postings — "
                      f"caught up, stopping pagination.")
                break

            time.sleep(LIST_REQUEST_DELAY_SECONDS)

        list_page.close()

        if len(new_listing_fields) > MAX_NEW_DETAIL_FETCHES_PER_RUN:
            print(f"  {len(new_listing_fields)} new postings found, capping detail fetches at "
                  f"{MAX_NEW_DETAIL_FETCHES_PER_RUN} for this run (the rest will be picked up "
                  f"next run — they're still new, so they stay in the queue).")
            to_fetch = new_listing_fields[:MAX_NEW_DETAIL_FETCHES_PER_RUN]
        else:
            to_fetch = new_listing_fields

        detail_page = browser.new_page()
        rows = []
        for fields in to_fetch:
            try:
                raw = fetch_detail_page(detail_page, fields["source_url"])
                detail_fields = parse_detail_text(raw)
            except Exception as e:
                print(f"  Detail fetch failed for {fields['source_url']}: {e}")
                detail_fields = None
            rows.append(structure_job(fields, detail_fields))
            time.sleep(DETAIL_REQUEST_DELAY_SECONDS)

        browser.close()

    if rows:
        append_rows(output_path, rows)
        print(f"Added {len(rows)} new postings. Workbook: {output_path}")
    else:
        append_rows(output_path, [])  # still logs a zero-new-row run
        print("No new postings this run.")


if __name__ == "__main__":
    main()
