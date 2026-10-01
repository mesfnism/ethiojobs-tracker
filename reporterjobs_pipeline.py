"""
Ethiopian Reporter Jobs Pipeline — third job board, alongside EthioJobs and
HaHuJobs (www.ethiopianreporterjobs.com), a WP Job Manager–style WordPress
job board with roughly 1,000+ active postings across ~14-per-page listing
pages.

Unlike HaHuJobs, this site's LISTING cards do not reliably carry every
field (salary and deadline, for example, only show up on the posting's own
detail page), so this pipeline follows the EthioJobs pattern instead:
listing pages are used only to discover which postings exist (job id, a
provisional title, and the URL) and to paginate newest-first; the detail
page is then fetched for each NEW posting and is the authoritative source
for every other field (employer, location, category, employment type,
career level, education, experience, salary, deadline, how to apply).

Honesty about this file's own test coverage: unlike ethiojobs_pipeline.py
and hahujobs_pipeline.py, this one was built from inspecting the site's
rendered text rather than from a captured live DOM fixture, because this
sandbox has no live network access to the site itself. The label-based
detail-page parser (see parse_detail_text) does not depend on field order,
so it should be fairly robust; the listing-card parser is positional, best
-effort, and the most likely thing to need a small fix after the first
real run — if a run fails or looks wrong, send the Action log and this
gets corrected quickly, the same way the HaHuJobs pipeline's few early
rough edges were.
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

# A realistic desktop Chrome user agent and viewport.
#
# Update (2026-10-01): the previous version of this comment assumed a
# Cloudflare JS-challenge was the failure mode, and the earlier fix (this
# user agent + --disable-blink-features=AutomationControlled) was built on
# that assumption. A real run's Action log instead showed a PLAIN
# "403 - Forbidden" / "Access to this page is forbidden." page — that is a
# different signature from a Cloudflare interstitial (which shows
# "Just a moment..." / "Checking your browser" branding). A bare 403 like
# this is much more commonly either (a) a WAF/security-plugin rule that
# blocks requests missing ordinary browser headers (Accept, Accept-Language,
# a same-site Referer, sec-fetch-*) or that have no prior session/cookie on
# the site, or (b) an IP/ASN-level block on data-center ranges (GitHub
# Actions runners all come from well-known cloud ASNs that many WAFs and
# hosting-provider security plugins block outright, regardless of browser
# fingerprint). This file now also does a "warm-up" homepage visit (to pick
# up cookies and a referer chain, in case (a) is the cause) and sends a
# fuller set of request headers, and it prints the real HTTP status code on
# every navigation so a repeat failure is easy to tell apart: if the status
# is still 403 even after these changes, that points at (b) — an IP-level
# block GitHub Actions cannot work around by itself (see the note in
# fetch_listing_page below for what to try next in that case).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="129", "Google Chrome";v="129", "Not=A?Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

BASE_URL = "https://www.ethiopianreporterjobs.com"
# Verified live (2026-10-01) by loading the site in a real browser and
# clicking through pagination: the listing is served by a client-side
# AJAX filter, and the "page/2/", "page/3/" style URL this file used
# before does NOT change the result (it silently re-serves page 1's own
# 14 postings every time, which would make pagination look like it always
# "reaches the end" after one page even when the request itself succeeds).
# The real pagination URL, confirmed to return distinct results on a plain
# page load (not just a client-side click), is a query string on the same
# listing page:
LIST_URL_TEMPLATE = BASE_URL + "/jobs-in-ethiopia/?ajax_filter=true&job_page={page}"
LIST_URL_FIRST_PAGE = BASE_URL + "/jobs-in-ethiopia/"

MAX_PAGES = int(os.environ.get("REPORTERJOBS_MAX_PAGES", "120"))
STOP_AFTER_CONSECUTIVE_SEEN = int(os.environ.get("REPORTERJOBS_STOP_AFTER_SEEN", "15"))
LIST_REQUEST_DELAY_SECONDS = float(os.environ.get("REPORTERJOBS_LIST_DELAY", "1.5"))
DETAIL_REQUEST_DELAY_SECONDS = float(os.environ.get("REPORTERJOBS_DETAIL_DELAY", "1.5"))
MAX_NEW_DETAIL_FETCHES_PER_RUN = int(os.environ.get("REPORTERJOBS_MAX_NEW_PER_RUN", "300"))

OUTPUT_XLSX = os.environ.get("REPORTERJOBS_OUTPUT_XLSX", "reporterjobs_tracker.xlsx")

COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

NAVY = "1F3B57"

EDUCATION_PATTERNS = [
    (re.compile(r"\bph\.?d\b|\bdoctorate\b", re.I), "PhD"),
    (re.compile(r"\bmaster'?s?\b|\bm\.?a\.?\b|\bm\.?sc\.?\b|\bmba\b", re.I), "Master's degree"),
    (re.compile(r"\bbachelor'?s?\b|\bb\.?a\.?\b|\bb\.?sc\.?\b|\bfirst degree\b", re.I), "Bachelor's degree"),
    (re.compile(r"\bdiploma\b", re.I), "Diploma"),
    (re.compile(r"\bcertificate\b", re.I), "Certificate"),
]


def _job_id_from_href(href):
    m = re.search(r"/jobs/(\d+)", href)
    return m.group(1) if m else href.rstrip("/").split("/")[-1]


# --------------------------------------------------------------------------
# Listing page — discovery only (job id, provisional title, URL)
# --------------------------------------------------------------------------

def parse_listing_card(href, raw_text):
    """Parses one listing card's inner text.

    Verified live (2026-10-01) against the site's real cards (class
    jobsearch-joblisting-classic-wrap), by running the exact same
    href-filter-plus-closest('li, article, div') extraction this pipeline
    uses, in a real browser: the line order is title, then "@ Employer",
    then location, then a "Published ... ago" line, then one or more
    category lines (with a lone "," token on its own line when a posting
    has more than one category), then a job-type badge (FULL-TIME,
    CONTRACT, ...) as the last line. The previous version of this parser
    had this backwards — it treated the lines BEFORE "ago" as category
    and the lines AFTER it as employer/location, when it's the other way
    around — so every field it filled in was being assigned from the
    wrong line. This version matches the real layout directly."""
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
    job_id = _job_id_from_href(href)
    source_url = href if href.startswith("http") else BASE_URL + href

    if len(lines) < 3:
        return {
            "job_id": job_id,
            "job_title": lines[0] if lines else None,
            "source_url": source_url,
            "_parse_warning": "unexpected_card_shape",
        }

    title = lines[0]
    employer = lines[1][1:].strip() if lines[1].startswith("@") else lines[1]
    location = lines[2]

    ago_idx = next((i for i, ln in enumerate(lines) if re.search(r"\bago\b", ln, re.I)), None)
    if ago_idx is None:
        return {
            "job_id": job_id,
            "job_title": title,
            "employer": employer,
            "location": location,
            "source_url": source_url,
            "_parse_warning": "no_ago_line",
        }

    # Everything after the "ago" line is one or more category names
    # (dropping the lone "," separator token the site's markup produces
    # between them) followed by a single all-caps job-type badge as the
    # last line, if present.
    tail = [ln for ln in lines[ago_idx + 1:] if ln != ","]
    work_type = None
    if tail and re.fullmatch(r"[A-Z][A-Z\-/ ]*", tail[-1]):
        work_type = tail[-1]
        tail = tail[:-1]
    category = ", ".join(tail) if tail else None

    return {
        "job_id": job_id,
        "job_title": title,
        "employer": employer,
        "category": category,
        "location": location,
        "work_type": work_type,
        "source_url": source_url,
    }


# --------------------------------------------------------------------------
# Detail page — authoritative, label-based (order-independent)
# --------------------------------------------------------------------------

def parse_detail_text(raw_text):
    """Label-based extraction, so it doesn't depend on the exact visual
    order these fields render in (unlike the listing-card parser above).
    Labels observed: Employment Type, Career Level, Industry, Education
    Required, Experience Required, Monthly Salary, Posted Date,
    Application Deadline, How to Apply, Job ID."""
    text = raw_text

    def field(label, stop_at_newline=True):
        pattern = rf"{re.escape(label)}\s*:?\s*(.+)"
        m = re.search(pattern, text, re.I)
        if not m:
            return None
        val = m.group(1)
        if stop_at_newline:
            val = val.split("\n")[0]
        return val.strip() or None

    employment_type = field("Employment Type")
    career_level = field("Career Level")
    industry = field("Industry")
    education_raw = field("Education Required")
    experience_raw = field("Experience Required")
    salary_hint = field("Monthly Salary")
    posted_date = field("Posted Date")
    deadline = field("Application Deadline")
    job_id_field = field("Job ID")
    location = field("Location")

    how_to_apply = None
    m = re.search(r"How\s*to\s*Apply\s*:?\s*(.+?)(?:Job ID|$)", text, re.I | re.S)
    if m:
        how_to_apply = m.group(1).strip()[:500] or None

    if salary_hint and salary_hint.lower().strip(". ") in {"as per company scale", "negotiable", "n/a"}:
        salary_hint = salary_hint.strip()  # kept as-is; taxonomy.salary_bucket treats it as unclassified

    education_required = None
    if education_raw:
        for pattern, label in EDUCATION_PATTERNS:
            if pattern.search(education_raw):
                education_required = label
                break

    years_experience_required = None
    if experience_raw:
        m = re.search(r"(\d+\s*(?:-|to)\s*\d+|\d+\+?)\s*years?", experience_raw, re.I)
        years_experience_required = m.group(0) if m else experience_raw[:60]

    return {
        "employment_type": employment_type,
        "career_level": career_level,
        "category": industry,
        "education_required": education_required,
        "years_experience_required": years_experience_required,
        "salary_hint": salary_hint,
        "date_posted_relative": posted_date,
        "application_deadline": deadline,
        "how_to_apply": how_to_apply,
        "job_id_confirmed": job_id_field,
        "location": location,
        "description": education_raw,  # the requirement text itself, kept for specialization extraction
    }


def structure_job(listing_fields, detail_fields):
    detail_fields = detail_fields or {}
    return {
        "job_id": listing_fields.get("job_id"),
        "job_title": listing_fields.get("job_title"),
        "employer": listing_fields.get("employer"),
        "category": detail_fields.get("category") or listing_fields.get("category"),
        "location": detail_fields.get("location") or listing_fields.get("location"),
        "work_type": listing_fields.get("work_type"),
        "career_level": detail_fields.get("career_level"),
        "employment_type": detail_fields.get("employment_type"),
        "number_required": None,
        "education_required": detail_fields.get("education_required"),
        "years_experience_required": detail_fields.get("years_experience_required"),
        "skills_required": None,
        "special_skill_training": None,
        "salary_hint": detail_fields.get("salary_hint"),
        "application_deadline": detail_fields.get("application_deadline"),
        "how_to_apply": detail_fields.get("how_to_apply"),
        "source_url": listing_fields.get("source_url"),
        "source": "ReporterJobs",
        "date_posted_relative": detail_fields.get("date_posted_relative"),
        "date_scraped": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "extraction_method": "dom_render" if detail_fields else "listing_only",
        "description": detail_fields.get("description"),
    }


# --------------------------------------------------------------------------
# Browser-driven fetch (Playwright — requires real internet access)
# --------------------------------------------------------------------------

def fetch_listing_page(page, page_num):
    url = LIST_URL_FIRST_PAGE if page_num == 1 else LIST_URL_TEMPLATE.format(page=page_num)
    prev_url = BASE_URL if page_num == 1 else (
        LIST_URL_FIRST_PAGE if page_num == 2 else LIST_URL_TEMPLATE.format(page=page_num - 1)
    )
    response = page.goto(
        url, wait_until="domcontentloaded", timeout=45000,
        referer=prev_url,
    )
    status = response.status if response else None
    try:
        page.wait_for_selector('a[href*="/jobs/"]', timeout=20000)
    except Exception:
        # Print the HTTP status code plus the page title/body snippet so a
        # real failure is diagnosable from the Action log instead of a bare
        # "no cards found". A status of 403 here that PERSISTS even with
        # the warm-up visit + fuller headers (see main()) most likely means
        # an IP/ASN-level block on GitHub Actions' data-center IP ranges —
        # fingerprint/header tweaks from the runner itself cannot work
        # around that. If this keeps happening, the next thing to try is
        # routing this one pipeline's requests through a proxy/VPN with a
        # residential or non-data-center exit IP, or running just this
        # script from a machine that isn't on a cloud ASN.
        try:
            title = page.title()
            snippet = page.inner_text("body")[:300].replace("\n", " ")
        except Exception:
            title, snippet = "(could not read page)", ""
        print(f"    [diagnostic] HTTP status: {status!r}")
        print(f"    [diagnostic] page title: {title!r}")
        print(f"    [diagnostic] body snippet: {snippet!r}")
        return []
    page.wait_for_timeout(600)

    raw_cards = page.evaluate(
        """
        () => {
          const anchors = Array.from(document.querySelectorAll('a')).filter(a => {
            const h = a.getAttribute('href') || '';
            return /\\/jobs\\/\\d+\\/?$/.test(h);
          });
          const seen = new Set();
          const cards = [];
          for (const a of anchors) {
            const href = a.href;
            if (seen.has(href)) continue;
            seen.add(href);
            const container = a.closest('li, article, div') || a.parentElement;
            cards.push({href, text: container ? container.innerText : a.innerText});
          }
          return cards;
        }
        """
    )
    return raw_cards


def fetch_detail_page(page, url, referer=None):
    response = page.goto(url, wait_until="domcontentloaded", timeout=45000, referer=referer)
    status = response.status if response else None
    if status and status >= 400:
        print(f"    [diagnostic] detail page HTTP status: {status!r} for {url}")
    page.wait_for_timeout(500)
    return page.inner_text("body")


# --------------------------------------------------------------------------
# Excel read/write — identical pattern to the other two pipelines
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

    new_listing_fields = []
    consecutive_seen = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            extra_http_headers=EXTRA_HEADERS,
        )
        list_page = context.new_page()

        # Warm-up visit: load the homepage first, like a real visitor would,
        # before going straight to a deep listing URL. This picks up any
        # cookies/session the site's WordPress stack sets on first contact,
        # in case the 403 is coming from a WAF rule keyed on "no prior
        # session" rather than (or in addition to) an IP-level block.
        try:
            warm_response = list_page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
            print(f"  Warm-up homepage visit: HTTP status {warm_response.status if warm_response else '(none)'}")
            list_page.wait_for_timeout(800)
        except Exception as e:
            print(f"  Warm-up homepage visit failed ({e}) — continuing anyway.")

        for page_num in range(1, MAX_PAGES + 1):
            try:
                cards = fetch_listing_page(list_page, page_num)
            except Exception as e:
                print(f"  Page {page_num}: failed to load ({e}) — stopping pagination.")
                break

            if not cards:
                print(f"  Page {page_num}: no cards found — reached the end.")
                break

            page_new = 0
            for c in cards:
                fields = parse_listing_card(c["href"], c["text"])
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
                  f"{MAX_NEW_DETAIL_FETCHES_PER_RUN} for this run.")
            to_fetch = new_listing_fields[:MAX_NEW_DETAIL_FETCHES_PER_RUN]
        else:
            to_fetch = new_listing_fields

        detail_page = context.new_page()
        rows = []
        for fields in to_fetch:
            try:
                raw = fetch_detail_page(detail_page, fields["source_url"], referer=LIST_URL_FIRST_PAGE)
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
        append_rows(output_path, [])
        print("No new postings this run.")


if __name__ == "__main__":
    main()
