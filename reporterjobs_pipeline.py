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
so it should be fairly robust; the listing-card parser is positional,
best-effort, and the most likely thing to need a small fix if the site's
markup changes — if a run looks wrong, send the run's log and this gets
corrected quickly.

Update (2026-10-02, part 1): a real run's Action log confirmed a plain
HTTP 403 on every listing-page request. This exposed an architectural bug
(now fixed): fetch_listing_page's "no cards found" return value was being
used for two different situations — "we were blocked" and "we genuinely
reached the end of the postings" — and main() treated both as the
harmless latter case, silently writing a valid-looking Excel file with
zero rows. ListingPageBlocked (below) now distinguishes these: an HTTP
error status on any page, or zero job links on page 1 specifically with
an otherwise-OK status, both raise and abort the whole run (no workbook
write at all) rather than writing a misleading empty tracker.

Update (2026-10-02, part 2): that 403 was then confirmed to NOT be an
IP/ASN block — the same 403 reproduced from a residential/university
connection (not just GitHub Actions), in both headless and fully visible
("headed") Playwright-driven Chromium, while the exact same URL loaded
fine seconds later through an ordinary, non-automated real Chrome browser
on what was effectively the same network. That rules out IP reputation
and headless-detection as the cause and points at something that targets
browser-automation tooling specifically: Playwright and Puppeteer both
drive the browser through the Chrome DevTools Protocol (CDP), which
leaves detectable fingerprints a WAF/security plugin can flag even in a
fully visible, "real-looking" browser window — independent of headless
mode, user-agent, or IP reputation.

The fix: this file no longer uses a real automated browser at all. It
fetches plain HTTP requests with curl_cffi, which impersonates a genuine
Chrome's TLS/JA3 fingerprint and header set at the network level without
driving any browser or using CDP — there is no automation protocol
signature for a CDP-fingerprinting rule to catch, because no automation
protocol is involved. The returned HTML is then parsed directly with
BeautifulSoup (no JavaScript execution at all). This works because this
site's listing and detail pages are server-rendered HTML — confirmed by
inspecting the real listing URL in a real browser, including the
"ajax_filter=true" listing URL, which returns fully-formed job cards
without needing any client-side rendering step.

If this new approach is STILL blocked, that would mean the detection
operates at the TLS/network layer in a way curl_cffi's impersonation
doesn't match (less likely, since curl_cffi is built specifically to
reproduce real Chrome's fingerprint), or there's a layer not yet
identified — at that point the pragmatic move is probably to stop
investing further here and let this source stay "not yet recovered," as
the intelligence report already states honestly, rather than one more
round of increasingly exotic workarounds.
"""

import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - reported plainly in main(), not a silent skip
    curl_requests = None

# curl_cffi's "impersonate" setting reproduces a real Chrome release's
# TLS/JA3 fingerprint AND a matching header set (User-Agent, sec-ch-ua,
# Accept, etc.) together, so this file does not set its own User-Agent or
# sec-ch-ua headers separately — a hand-set header that doesn't match the
# TLS fingerprint's real Chrome version is itself a known detection signal,
# the opposite of what a spoofed header set is supposed to achieve.
IMPERSONATE = os.environ.get("REPORTERJOBS_IMPERSONATE", "chrome124")

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
REQUEST_TIMEOUT_SECONDS = 30

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

_JOB_HREF_RE = re.compile(r"/jobs/\d+/?$")


class ListingPageBlocked(Exception):
    """Raised when a listing-page fetch looks like a block/failure rather
    than a genuine end-of-listings. Distinguishing these two matters: the
    pipeline must never treat "the site refused us" the same as "there are
    no more postings," because both currently produce the same symptom
    (zero cards) and writing that through as an empty tracker would make a
    scraping failure look like real labor-market information (zero new
    vacancies) to every downstream report. See fetch_listing_page below for
    where this is raised, and main() for why it aborts the whole run
    (sys.exit(1), no workbook write) instead of silently continuing."""

    def __init__(self, page_num, status, reason):
        self.page_num = page_num
        self.status = status
        self.reason = reason
        super().__init__(f"page {page_num}, HTTP status {status!r}: {reason}")


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
    wrong line. This version matches the real layout directly.

    BeautifulSoup's get_text(separator="\\n", strip=True) (used by
    fetch_listing_page below) produces the same line-per-visible-text-node
    shape this parser was built against, since it was captured from the
    real, server-rendered markup rather than from anything JavaScript
    assembled at runtime — so no change was needed here when the fetch
    layer switched from a real browser's innerText to BeautifulSoup."""
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
# Plain-HTTP fetch via curl_cffi (TLS-fingerprint impersonation, no
# browser, no CDP — see this file's module docstring for why) + direct
# HTML parsing via BeautifulSoup (no JavaScript execution at all, since
# this site's listing and detail pages are server-rendered).
# --------------------------------------------------------------------------

def _extract_cards(html, base_url):
    """Finds every anchor whose href matches /jobs/<id>/, dedups by URL,
    and for each one returns {href, text} where text is the visible text
    of its closest li/article/div ancestor — the same shape (and, for
    server-rendered HTML, very nearly the same text) that the earlier
    Playwright-based version produced via closest('li, article, div') +
    .innerText, so parse_listing_card needed no changes."""
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    cards = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _JOB_HREF_RE.search(href):
            continue
        full_href = urljoin(base_url, href)
        if full_href in seen:
            continue
        seen.add(full_href)
        container = a.find_parent(["li", "article", "div"]) or a
        text = container.get_text(separator="\n", strip=True)
        cards.append({"href": full_href, "text": text})
    return cards


def fetch_listing_page(session, page_num):
    url = LIST_URL_FIRST_PAGE if page_num == 1 else LIST_URL_TEMPLATE.format(page=page_num)
    referer = BASE_URL if page_num == 1 else (
        LIST_URL_FIRST_PAGE if page_num == 2 else LIST_URL_TEMPLATE.format(page=page_num - 1)
    )
    resp = session.get(url, headers={"Referer": referer}, timeout=REQUEST_TIMEOUT_SECONDS)
    status = resp.status_code

    # An explicit HTTP error status (confirmed live on 2026-10-01: a bare
    # 403 from this site) is never a legitimate "no more postings" signal
    # on ANY page, so it always raises rather than being read as an empty
    # listing.
    if status is None or status >= 400:
        raise ListingPageBlocked(page_num, status, "request returned an HTTP error status")

    cards = _extract_cards(resp.text, url)

    if not cards and page_num == 1:
        # Zero job links on the very first listing page, with an
        # otherwise-OK HTTP status, almost never means "zero active
        # postings" on a board that normally runs 1,000+ of them — it
        # means something is wrong (a soft block that still returns 200,
        # or a layout/markup change this parser no longer matches).
        # Treating this as "reached the end" would silently produce an
        # empty tracker that looks identical to a genuinely quiet day.
        snippet = resp.text[:300].replace("\n", " ")
        print(f"    [diagnostic] HTTP status: {status!r}")
        print(f"    [diagnostic] body snippet: {snippet!r}")
        raise ListingPageBlocked(
            page_num, status,
            "no job links found on the first listing page; treated as a failure, not an empty listing",
        )

    return cards


def fetch_detail_page(session, url, referer=None):
    headers = {"Referer": referer} if referer else {}
    resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    if resp.status_code and resp.status_code >= 400:
        print(f"    [diagnostic] detail page HTTP status: {resp.status_code!r} for {url}")
        raise RuntimeError(f"HTTP {resp.status_code} fetching {url}")
    soup = BeautifulSoup(resp.text, "html.parser")
    body = soup.body or soup
    return body.get_text(separator="\n", strip=True)


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
    if curl_requests is None:
        print("curl_cffi is not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    output_path = Path(OUTPUT_XLSX)
    existing_ids = load_existing_ids(output_path)
    print(f"Loaded {len(existing_ids)} existing job IDs from {output_path}")

    new_listing_fields = []
    consecutive_seen = 0

    session = curl_requests.Session(impersonate=IMPERSONATE)

    # Warm-up visit: load the homepage first, like a real visitor would,
    # before going straight to a deep listing URL. This picks up any
    # cookies/session the site's WordPress stack sets on first contact
    # (the Session object keeps cookies across requests automatically),
    # and is cheap diagnostic signal on its own: if THIS already comes
    # back as an error status, the block is happening before any listing
    # logic is even involved.
    try:
        warm_resp = session.get(BASE_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        print(f"  Warm-up homepage visit: HTTP status {warm_resp.status_code}")
    except Exception as e:
        print(f"  Warm-up homepage visit failed ({e}) — continuing anyway.")

    for page_num in range(1, MAX_PAGES + 1):
        try:
            cards = fetch_listing_page(session, page_num)
        except ListingPageBlocked as e:
            print(f"  Page {page_num}: BLOCKED/FAILED ({e}) — aborting this run without writing "
                  f"anything, rather than committing a tracker that would look like a legitimate "
                  f"'zero new postings' day.")
            sys.exit(1)
        except Exception as e:
            if page_num == 1:
                # The very first page failing to even load (a connection
                # error, a timeout) is the same "can't trust this run"
                # situation as ListingPageBlocked above, just from a
                # different kind of exception — so it gets the same
                # hard-abort treatment rather than silently producing an
                # empty-looking result.
                print(f"  Page {page_num}: failed to load ({e}) — aborting this run without writing "
                      f"anything (see reporterjobs_pipeline.py's module docstring/ListingPageBlocked "
                      f"for why page 1 is treated this strictly).")
                sys.exit(1)
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

    if len(new_listing_fields) > MAX_NEW_DETAIL_FETCHES_PER_RUN:
        print(f"  {len(new_listing_fields)} new postings found, capping detail fetches at "
              f"{MAX_NEW_DETAIL_FETCHES_PER_RUN} for this run.")
        to_fetch = new_listing_fields[:MAX_NEW_DETAIL_FETCHES_PER_RUN]
    else:
        to_fetch = new_listing_fields

    rows = []
    for fields in to_fetch:
        try:
            raw = fetch_detail_page(session, fields["source_url"], referer=LIST_URL_FIRST_PAGE)
            detail_fields = parse_detail_text(raw)
        except Exception as e:
            print(f"  Detail fetch failed for {fields['source_url']}: {e}")
            detail_fields = None
        rows.append(structure_job(fields, detail_fields))
        time.sleep(DETAIL_REQUEST_DELAY_SECONDS)

    if rows:
        append_rows(output_path, rows)
        print(f"Added {len(rows)} new postings. Workbook: {output_path}")
    else:
        append_rows(output_path, [])
        print("No new postings this run.")


if __name__ == "__main__":
    main()
