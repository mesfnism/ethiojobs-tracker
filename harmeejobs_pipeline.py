"""
HarmeeJobs Pipeline — sixth job board (harmeejobs.com), the last in the
originally agreed build order (palmjobs.et -> ezega.com -> devnetjobs.org
-> harmeejobs.com).

Unlike every other site in this project, this one isn't scraped from
rendered HTML at all: harmeejobs.com is a WordPress + WP Job Manager site
that publishes its own dedicated job RSS feed at
https://harmeejobs.com/?feed=job_feed — a real, namespaced
(xmlns:job_listing="https://harmeejobs.com") syndication feed meant to be
machine-read, confirmed live. Each <item> already carries, as clean XML
elements, the employer, location, job type and category
(job_listing:company / :location / :job_type / :job_category), the real
publish timestamp (pubDate — an actual date, not a site's relative
"X days ago" text like every other source in this project has to work
with), a stable numeric post ID (in <guid>, as ?p=NNNNN), the canonical
posting URL, and the FULL posting body as HTML in <content:encoded>. This
makes it the most structurally reliable source built so far: no listing-
vs-detail-page split, no postback replay, no DOM rendering needed at
all — just an XML GET request.

Pagination is ?feed=job_feed&paged=N (confirmed live: page 2 returns a
different, non-overlapping set of post IDs; a page past the end returns
zero <item> elements, which is this pipeline's stop condition).

Skills: each posting's content HTML includes a distinct "Requirement
Skill" section rendered as a list of Material-UI chips
(class="...MuiListItemText-primary...") — this pipeline pulls those
directly as skills_required. Like every other source in this project,
some of these chip values are themselves full sentences rather than
clean skill names (e.g. "Strong organizational and time-management
skills." sitting next to "Organization & Time Management" for the same
posting) — this is left to taxonomy.normalize_skill()'s existing noise
filter downstream in the rollup, exactly as for every other source,
rather than re-implemented here.

Honest gaps: no salary field is exposed anywhere in the feed or the
postings inspected while building this, so salary_hint is only ever
filled when a figure is found written into the posting's own free text
(rare) — never guessed. Education level and years of experience aren't
separate structured fields either; both are extracted from the posting's
free-text body with the same best-effort regexes already used by every
other pipeline in this project for the same reason.
"""

import html as html_module
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

BASE_URL = "https://harmeejobs.com"
FEED_URL_TEMPLATE = BASE_URL + "/?feed=job_feed&paged={page}"

MAX_PAGES = int(os.environ.get("HARMEEJOBS_MAX_PAGES", "40"))
STOP_AFTER_CONSECUTIVE_SEEN = int(os.environ.get("HARMEEJOBS_STOP_AFTER_SEEN", "15"))
REQUEST_DELAY_SECONDS = float(os.environ.get("HARMEEJOBS_REQUEST_DELAY", "1.0"))

OUTPUT_XLSX = os.environ.get("HARMEEJOBS_OUTPUT_XLSX", "harmeejobs_tracker.xlsx")

COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

NAVY = "1F3B57"

JOB_NS = "https://harmeejobs.com"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

GUID_ID_RE = re.compile(r"p=(\d+)")
SKILL_SPAN_RE = re.compile(r'MuiListItemText-primary[^"]*">([^<]+)</span>', re.S)
REQUIRED_NUMBER_RE = re.compile(r"Required Number:\s*([^\n<]+)", re.I)
DEADLINE_RE = re.compile(r"Application Deadline:\s*([^\n<]+)", re.I)
EXPERIENCE_RE = re.compile(r"Experience[:\s]*([^\n<]*\byears?\b[^\n<]*)", re.I)
SALARY_RE = re.compile(r"(?:salary|remuneration|pay)[^\n<]{0,40}?((?:ETB|Birr|USD|\$)\s?[\d,]+(?:\s?-\s?[\d,]+)?)", re.I)

EDUCATION_PATTERNS = [
    (re.compile(r"\bph\.?d\b|\bdoctorate\b", re.I), "PhD"),
    (re.compile(r"\bmaster'?s?\b|\bm\.?a\.?\b|\bm\.?sc\.?\b|\bmba\b", re.I), "Master's degree"),
    (re.compile(r"\bbachelor'?s?\b|\bb\.?a\.?\b|\bb\.?sc\.?\b|\bfirst degree\b", re.I), "Bachelor's degree"),
    (re.compile(r"\bdiploma\b", re.I), "Diploma"),
    (re.compile(r"\bcertificate\b|\btvet\b", re.I), "Certificate"),
]

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_TAG_RE = re.compile(r"</(p|div|li|h\d)\s*>|<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"[ \t]*\n[ \t]*")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def strip_tags_to_text(fragment_html):
    text = _SCRIPT_STYLE_RE.sub(" ", fragment_html)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text)
    text = _BLANK_LINES_RE.sub("\n", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def job_id_from_guid(guid_text):
    if not guid_text:
        return None
    m = GUID_ID_RE.search(guid_text)
    return m.group(1) if m else None


def extract_feed_skills(content_html):
    """The "Requirement Skill" section's Material-UI chip values — a mix
    of clean skill names and, for some postings, full sentences; left
    as-is here, same noise filter as every other source applies them
    downstream (taxonomy.normalize_skill)."""
    skills = []
    for raw in SKILL_SPAN_RE.findall(content_html):
        s = html_module.unescape(raw).strip()
        if s:
            skills.append(s)
    return skills


def extract_how_to_apply(content_html):
    idx = content_html.find('id="how-to-apply-section"')
    if idx == -1:
        return None
    text = strip_tags_to_text(content_html[idx:])
    text = re.sub(r"^How\s*To\s*Apply\s*\n*", "", text, flags=re.I)
    return text.strip() or None


def extract_description(content_html):
    """Plain text of the "About the Job" / "About You" sections — cut off
    before the "Requirement Skill" chip list and the "How To Apply"
    section, both handled by their own extractors, so neither gets
    duplicated into the free-text description."""
    idx_skill = content_html.find("Requirement Skill")
    idx_apply = content_html.find('id="how-to-apply-section"')
    candidates = [i for i in (idx_skill, idx_apply) if i != -1]
    cutoff = min(candidates) if candidates else len(content_html)
    text = strip_tags_to_text(content_html[:cutoff])
    return text or None


def classify_education(text):
    if not text:
        return None
    for pattern, label in EDUCATION_PATTERNS:
        if pattern.search(text):
            return label
    return None


def extract_experience(text):
    if not text:
        return None
    m = EXPERIENCE_RE.search(text)
    return m.group(1).strip() if m else None


def extract_required_number(text):
    if not text:
        return None
    m = REQUIRED_NUMBER_RE.search(text)
    return m.group(1).strip() if m else None


def extract_deadline(text):
    if not text:
        return None
    m = DEADLINE_RE.search(text)
    return m.group(1).strip() if m else None


def extract_salary_hint(text):
    """Honest gap, not a guess: no structured salary field exists
    anywhere in this feed or the postings inspected while building this
    pipeline, so this only ever fires if a figure is written directly
    into a posting's own free text."""
    if not text:
        return None
    m = SALARY_RE.search(text)
    return m.group(1).strip() if m else None


def structure_job(item):
    guid_el = item.find("guid")
    job_id = job_id_from_guid(guid_el.text if guid_el is not None else None)
    if not job_id:
        return None

    title_el = item.find("title")
    link_el = item.find("link")
    pubdate_el = item.find("pubDate")
    content_el = item.find(f"{{{CONTENT_NS}}}encoded")

    location_el = item.find(f"{{{JOB_NS}}}location")
    job_type_el = item.find(f"{{{JOB_NS}}}job_type")
    job_category_el = item.find(f"{{{JOB_NS}}}job_category")
    company_el = item.find(f"{{{JOB_NS}}}company")

    content_html = content_el.text or "" if content_el is not None else ""
    description = extract_description(content_html)
    deadline = extract_deadline(description) or extract_deadline(content_html)

    date_posted = None
    if pubdate_el is not None and pubdate_el.text:
        try:
            date_posted = parsedate_to_datetime(pubdate_el.text).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            date_posted = None

    return {
        "job_id": job_id,
        "job_title": (title_el.text or "").strip() if title_el is not None and title_el.text else None,
        "employer": (company_el.text or "").strip() if company_el is not None and company_el.text else None,
        "category": (job_category_el.text or "").strip() if job_category_el is not None and job_category_el.text else None,
        "location": (location_el.text or "").strip() if location_el is not None and location_el.text else None,
        "work_type": None,
        "career_level": None,
        "employment_type": (job_type_el.text or "").strip() if job_type_el is not None and job_type_el.text else None,
        "number_required": extract_required_number(description),
        "education_required": classify_education(description),
        "years_experience_required": extract_experience(description),
        "skills_required": "; ".join(extract_feed_skills(content_html)) or None,
        "special_skill_training": None,
        "salary_hint": extract_salary_hint(description),
        "application_deadline": deadline,
        "how_to_apply": extract_how_to_apply(content_html),
        "source_url": link_el.text.strip() if link_el is not None and link_el.text else None,
        "source": "HarmeeJobs",
        # the feed's own pubDate is a real timestamp, not relative text
        # like every other source — stored here as a plain date string,
        # which is strictly more precise than what this field holds for
        # any other source in this project.
        "date_posted_relative": date_posted,
        "date_scraped": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "extraction_method": "rss_feed",
        "description": description,
    }


# --------------------------------------------------------------------------
# Feed fetch — plain GET + XML parse, no browser rendering needed at all.
# --------------------------------------------------------------------------

def fetch_feed_page(request_context, page_num):
    url = FEED_URL_TEMPLATE.format(page=page_num)
    resp = request_context.get(url, timeout=30000)
    if resp.status != 200:
        raise RuntimeError(f"Unexpected status {resp.status} fetching {url}")
    root = ET.fromstring(resp.text())
    channel = root.find("channel")
    return channel.findall("item") if channel is not None else []


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
        request_context = p.request.new_context()

        for page_num in range(1, MAX_PAGES + 1):
            try:
                items = fetch_feed_page(request_context, page_num)
            except Exception as e:
                print(f"  Page {page_num}: failed to fetch/parse ({e}) — stopping pagination.")
                break

            if not items:
                print(f"  Page {page_num}: no items — reached the end.")
                break

            page_new = 0
            for item in items:
                job = structure_job(item)
                if not job or not job["job_id"]:
                    continue
                if job["job_id"] in existing_ids:
                    consecutive_seen += 1
                else:
                    consecutive_seen = 0
                    new_rows.append(job)
                    existing_ids.add(job["job_id"])
                    page_new += 1

            print(f"  Page {page_num}: {len(items)} postings, {page_new} new, "
                  f"{consecutive_seen} consecutive already-seen.")

            if consecutive_seen >= STOP_AFTER_CONSECUTIVE_SEEN:
                print(f"  Hit {STOP_AFTER_CONSECUTIVE_SEEN} consecutive already-seen postings — "
                      f"caught up, stopping pagination.")
                break
            time.sleep(REQUEST_DELAY_SECONDS)

        request_context.dispose()

    if new_rows:
        append_rows(output_path, new_rows)
        print(f"Added {len(new_rows)} new postings. Workbook: {output_path}")
    else:
        append_rows(output_path, [])
        print("No new postings this run.")


if __name__ == "__main__":
    main()
