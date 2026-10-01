"""
HaHuJobs Pipeline — companion scraper to ethiojobs_pipeline.py, covering
HaHuJobs (hahu.jobs), another major Ethiopian general job board.

Unlike EthioJobs, every field HaHuJobs needs is already present on its
LISTING pages (no separate detail-page fetch required per posting) — the
listing card itself contains employer, title, two-level category, location,
experience, positions, employment type, and the full duties/description
text. That makes this pipeline simpler and faster than the EthioJobs one:
no per-new-posting detail visits, just a walk through the paginated
listing.

Writes to its own tracker (hahujobs_tracker.xlsx), kept separate from
ethiojobs_tracker.xlsx so a bug or site redesign in one can never corrupt
the other's data. combine_trackers.py merges both into one dataset for the
dashboard.

Sort order on hahu.jobs's listing is not documented as "newest first" (it
looks deadline-driven, not posting-date-driven), so — unlike EthioJobs —
this pipeline does NOT use an early-stop-after-N-seen shortcut. It walks
every page every run and relies on job_id dedup to skip postings already
in the tracker. With ~1,200 total postings across ~23 pages, a full walk
is fast (no per-posting detail fetch) and this is the only way to be sure
nothing is missed.
"""

import re
import sys
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import sync_playwright

BASE_LIST_URL = "https://www.hahu.jobs/jobs?page={page}"
OUTPUT_XLSX = "hahujobs_tracker.xlsx"
MAX_PAGES = 60  # safety cap; the real run stops when a page has 0 cards

# Columns match ethiojobs_tracker.xlsx exactly, so combine_trackers.py can
# simply concatenate rows rather than map between two different schemas.
# Fields HaHuJobs doesn't structurally expose are left None — an honest
# gap rather than a guess.
COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

_EXPERIENCE_RE = re.compile(r"^\d+\s*years?(\s*-\s*\d+\s*years?)?$", re.I)
_POSITIONS_RE = re.compile(r"^\d+\s*positions?$", re.I)
_TYPE_RE = re.compile(r"^(Bid|Contract|Full Time|Internship|Part Time)$", re.I)
_SALARY_RE = re.compile(r"^Birr\s", re.I)

# Best-effort education-level extraction from the free-text description
# (HaHuJobs has no structured education field, unlike EthioJobs). Matched
# in the order each phrase is checked, first hit wins, mirroring how a
# posting typically leads with its own primary requirement. Left as None
# when nothing matches, rather than guessed at.
_EDUCATION_PATTERNS = [
    (re.compile(r"master'?s degree", re.I), "Master's degree"),
    (re.compile(r"\bphd\b|doctor of philosophy", re.I), "PhD"),
    (re.compile(r"bachelor'?s degree", re.I), "Bachelor's degree"),
    (re.compile(r"\bdiploma\b", re.I), "Diploma"),
    (re.compile(r"\btvet\b|\bcertificate\b", re.I), "Certificate"),
]


def extract_education(description):
    if not description:
        return None
    head = description[:200]  # the requirement phrase is always near the start
    for pattern, label in _EDUCATION_PATTERNS:
        if pattern.search(head):
            return label
    return None


def parse_card(href, text):
    """Parses one job card's innerText block into structured fields.
    Returns (fields_dict, ok) — ok=False means something didn't match the
    expected shape, so the row is kept as best-effort (extraction_method
    'partial') rather than dropped."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    ok = True

    job_id_match = re.search(r"/jobs/([^/?]+)", href)
    job_id = job_id_match.group(1) if job_id_match else href

    try:
        save_idx = lines.index("Save")
    except ValueError:
        save_idx = 1 if len(lines) > 1 else 0
        ok = False

    deadline_relative = lines[0] if save_idx > 0 else None

    block = lines[save_idx + 1:]
    if len(block) < 5:
        ok = False
        employer = block[0] if len(block) > 0 else None
        title = block[1] if len(block) > 1 else None
        cat1 = block[2] if len(block) > 2 else None
        cat2 = block[3] if len(block) > 3 else None
        location = block[4] if len(block) > 4 else None
        rest = []
    else:
        employer, title, cat1, cat2, location = block[:5]
        rest = block[5:]

    experience = positions = emp_type = salary = None
    idx = 0
    if idx < len(rest) and (_EXPERIENCE_RE.match(rest[idx]) or "year" in rest[idx].lower()):
        experience = rest[idx]
        idx += 1
    if idx < len(rest) and _POSITIONS_RE.match(rest[idx]):
        positions = rest[idx]
        idx += 1
    if idx < len(rest) and _TYPE_RE.match(rest[idx]):
        emp_type = rest[idx]
        idx += 1
    if idx < len(rest) and _SALARY_RE.match(rest[idx]):
        salary = rest[idx]
        idx += 1

    # Everything from here up to "Read More" is the description.
    desc_lines = []
    for l in rest[idx:]:
        if l == "Read More":
            break
        desc_lines.append(l)
    description = " ".join(desc_lines).strip() or None

    if experience is None or positions is None or emp_type is None:
        ok = False

    number_required = None
    if positions:
        m = re.search(r"\d+", positions)
        if m:
            number_required = int(m.group())

    category_parts = [c for c in (cat1, cat2) if c]
    category = "; ".join(category_parts) if category_parts else None

    fields = {
        "job_id": job_id,
        "job_title": title,
        "employer": employer,
        "category": category,
        "location": location,
        "work_type": None,
        "career_level": None,
        "employment_type": emp_type,
        "number_required": number_required,
        "education_required": extract_education(description),
        "years_experience_required": experience,
        "skills_required": None,
        "special_skill_training": None,
        "salary_hint": salary,
        "application_deadline": deadline_relative,
        "how_to_apply": None,
        "source_url": f"https://hahu.jobs/jobs/{job_id}",
        "source": "HaHuJobs",
        "date_posted_relative": None,
        "date_scraped": None,  # filled in by caller
        "extraction_method": "listing_only" if ok else "partial",
        # Kept (not just used transiently for extract_education above) so
        # the dashboard can extract education SPECIALIZATION (field of
        # study) from it later — EthioJobs has no equivalent field, so
        # that drill-down will only ever have data for HaHuJobs postings.
        "description": description,
    }
    return fields, ok


def fetch_page_cards(page, page_num):
    url = BASE_LIST_URL.format(page=page_num)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector('a[href*="/jobs/"]', timeout=20000)
    except Exception:
        return []
    page.wait_for_timeout(800)  # let any lazy content settle

    raw_cards = page.evaluate(
        """
        () => {
          const anchors = Array.from(document.querySelectorAll('a')).filter(a => {
            const h = a.getAttribute('href') || '';
            return h.includes('/jobs/') && !h.includes('app.hahu.jobs')
                   && h !== '/jobs' && !h.startsWith('/jobs?');
          });
          const seen = new Set();
          const cards = [];
          for (const a of anchors) {
            const href = a.href;
            if (seen.has(href)) continue;
            seen.add(href);
            const infoDiv = a.parentElement.parentElement;
            cards.push({href, text: infoDiv.innerText});
          }
          return cards;
        }
        """
    )
    return raw_cards


def load_existing_ids(path):
    try:
        wb = openpyxl.load_workbook(path)
    except FileNotFoundError:
        return set(), None
    ws = wb["Jobs"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    id_col = header.index("job_id")
    ids = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[id_col]:
            ids.add(row[id_col])
    return ids, wb


def new_workbook():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3B57")
    log = wb.create_sheet("Run Log")
    log.append(["run_timestamp_utc", "new_rows", "running_total", "pages_walked"])
    for cell in log[1]:
        cell.font = Font(bold=True)
    return wb


def append_rows(wb, rows):
    ws = wb["Jobs"]
    for r in rows:
        ws.append([r.get(col) for col in COLUMNS])


def log_run(wb, new_count, total_count, pages_walked):
    log = wb["Run Log"]
    log.append([
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        new_count,
        total_count,
        pages_walked,
    ])


def main():
    existing_ids, wb = load_existing_ids(OUTPUT_XLSX)
    if wb is None:
        wb = new_workbook()

    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    new_rows = []
    pages_walked = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for page_num in range(1, MAX_PAGES + 1):
            cards = fetch_page_cards(page, page_num)
            pages_walked = page_num
            if not cards:
                break
            for c in cards:
                fields, ok = parse_card(c["href"], c["text"])
                if not ok:
                    print(f"  [warn] partial parse for {fields.get('job_id')} "
                          f"on page {page_num}", file=sys.stderr)
                if fields["job_id"] in existing_ids:
                    continue
                fields["date_scraped"] = now_str
                new_rows.append(fields)
                existing_ids.add(fields["job_id"])
        browser.close()

    if new_rows:
        append_rows(wb, new_rows)

    total = wb["Jobs"].max_row - 1
    log_run(wb, len(new_rows), total, pages_walked)
    wb.save(OUTPUT_XLSX)
    print(f"Walked {pages_walked} pages, {len(new_rows)} new postings, "
          f"{total} total in {OUTPUT_XLSX}.")


if __name__ == "__main__":
    main()
