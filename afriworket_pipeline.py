"""
Afriworket Pipeline — companion scraper for afriworket.com/jobs, a
Nuxt-rendered job board (api.afriworket.com/v1/graphql is its backend,
confirmed live by inspecting network requests on 2026-10-01). The job
list is not paginated through a URL or a plain network-visible request
once rendered: clicking the page's own "Load More" button reveals ten
more already-fetched cards at a time with no new network request, so
this pipeline drives that same button in the browser rather than trying
to call the GraphQL endpoint directly, which would mean reverse
engineering an undocumented, unstable schema for no real benefit.

Two-phase pattern, the same as ethiojobs_pipeline.py: the listing page is
used only to discover job ids (each posting has its own URL,
/jobs/<uuid>), and every field is read from the richer detail page, which
carries structured fields the listing cards only show mixed into free
text (category, education qualification, vacancies, experience level,
work address, salary type).

Early stop: cards are sorted newest-first (confirmed by the deadlines and
relative "Posted ... ago" timestamps in page order), so once two
consecutive "Load More" clicks reveal only postings already in the
tracker, the pipeline stops clicking rather than re-walking the entire
multi-hundred-posting history on every run. A full backfill still happens
naturally the first time this runs against an empty tracker, bounded by
MAX_LOAD_MORE_CLICKS as a hard safety cap either way.
"""

import re
import sys
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import sync_playwright

SOURCE_NAME = "Afriworket"
BASE_URL = "https://afriworket.com"
LISTING_URL = BASE_URL + "/jobs"
OUTPUT_XLSX = "afriworket_tracker.xlsx"
MAX_LOAD_MORE_CLICKS = 60  # safety cap; early-stop below usually ends runs well before this
EARLY_STOP_AFTER_KNOWN_ROUNDS = 2  # consecutive all-already-seen batches before stopping

COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

_JOB_HREF_RE = re.compile(r"^/jobs/([0-9a-f-]{8,})$", re.I)


def job_id_from_href(href):
    m = _JOB_HREF_RE.match(href)
    return m.group(1) if m else href


def collect_job_hrefs(page):
    return page.eval_on_selector_all(
        'a[href^="/jobs/"]',
        "els => [...new Set(els.map(e => e.getAttribute('href')))]",
    )


def click_load_more(page):
    """Clicks the "Load More" button via JS (bypassing any overlay that
    would intercept a coordinate-based click) and returns True if a
    button was found and clicked, False once it's gone (end of list)."""
    clicked = page.evaluate(
        """
        () => {
          const btn = [...document.querySelectorAll('button')]
            .find(b => /load more/i.test(b.textContent || ''));
          if (!btn) return false;
          btn.scrollIntoView({block: 'center'});
          btn.click();
          return true;
        }
        """
    )
    return bool(clicked)


def discover_job_ids(page, existing_ids):
    page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector('a[href^="/jobs/"]', timeout=20000)
    except Exception:
        return []
    page.wait_for_timeout(600)

    seen_hrefs = set(collect_job_hrefs(page))
    all_hrefs = list(seen_hrefs)
    known_rounds = 0

    for _ in range(MAX_LOAD_MORE_CLICKS):
        delta_known = all(job_id_from_href(h) in existing_ids for h in seen_hrefs) if seen_hrefs else False
        if not click_load_more(page):
            break
        page.wait_for_timeout(500)
        new_hrefs = set(collect_job_hrefs(page))
        delta = new_hrefs - seen_hrefs
        if not delta:
            break
        all_hrefs.extend(delta)
        seen_hrefs = new_hrefs

        if all(job_id_from_href(h) in existing_ids for h in delta):
            known_rounds += 1
            if known_rounds >= EARLY_STOP_AFTER_KNOWN_ROUNDS:
                break
        else:
            known_rounds = 0

    return sorted(set(all_hrefs))


def parse_detail_text(raw_text):
    """Parses a detail page's body text into structured fields. Field
    order and shape (some as "Label: value" on one line, others as
    "value\\nLabel" with the label trailing) verified live on 2026-10-01
    against a real posting."""
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]

    posted_idx = next((i for i, ln in enumerate(lines) if ln.startswith("Posted ")), None)
    if posted_idx is None or posted_idx < 2:
        return {"_parse_warning": "unexpected_detail_shape"}

    posted_date = lines[posted_idx][len("Posted "):]
    category = lines[posted_idx - 1]
    title = lines[posted_idx - 2]
    location = lines[posted_idx + 1] if posted_idx + 1 < len(lines) else None

    def line_value(label):
        pattern = re.compile(rf"^{re.escape(label)}\s*:\s*(.+)$", re.I)
        for ln in lines:
            m = pattern.match(ln)
            if m:
                return m.group(1).strip()
        return None

    def value_before_label(label):
        try:
            idx = lines.index(label)
        except ValueError:
            return None, None
        return (lines[idx - 1] if idx > 0 else None), idx

    job_type = line_value("Job Type")
    deadline = line_value("Deadline")
    vacancies = line_value("Vacancies")
    education = line_value("Education Qualification")

    salary_type, salary_idx = value_before_label("Salary Type")
    salary_hint = None
    if salary_idx is not None and salary_idx >= 2:
        maybe_amount = lines[salary_idx - 2]
        if re.search(r"\bETB\b", maybe_amount, re.I):
            salary_hint = f"{maybe_amount} / {salary_type}" if salary_type else maybe_amount

    career_level, exp_idx = value_before_label("Experience Level")

    skills = []
    try:
        skills_idx = lines.index("Skills And Expertise")
        addr_idx = lines.index("Work Address")
        skills = lines[skills_idx + 1:addr_idx]
    except ValueError:
        addr_idx = None

    work_address = None
    desc_start = None
    if addr_idx is not None and addr_idx + 1 < len(lines):
        work_address = lines[addr_idx + 1]
        try:
            desc_start = lines.index("Job Description", addr_idx) + 1
        except ValueError:
            desc_start = None

    employer = None
    description = None
    jobs_posted_idx = next((i for i, ln in enumerate(lines) if re.match(r"^Jobs Posted\s*:\s*\d+$", ln, re.I)), None)
    if desc_start is not None and jobs_posted_idx is not None and jobs_posted_idx > desc_start:
        description = "\n".join(lines[desc_start:jobs_posted_idx - 1]).strip() or None
        employer = lines[jobs_posted_idx - 1]

    number_required = None
    if vacancies:
        m = re.search(r"\d+", vacancies)
        if m:
            number_required = int(m.group())

    return {
        "job_title": title,
        "employer": employer,
        "category": category,
        "location": location,
        "work_type": job_type,
        "career_level": career_level,
        "number_required": number_required,
        "education_required": education,
        "skills_required": "; ".join(skills) if skills else None,
        "salary_hint": salary_hint,
        "application_deadline": deadline,
        "date_posted_relative": posted_date,
        "work_address": work_address,
        "description": description,
    }


def structure_job(job_id, href, detail_fields):
    work_address = detail_fields.get("work_address")
    description_parts = []
    if detail_fields.get("description"):
        description_parts.append(detail_fields["description"])
    if work_address:
        # More granular than location (e.g. "Jimma, Agaro" vs "Jimma,
        # Ethiopia") and has no dedicated column in this project's shared
        # schema, so it's appended here rather than silently dropped.
        description_parts.append(f"Work address: {work_address}")
    description = "\n\n".join(description_parts) or None

    return {
        "job_id": job_id,
        "job_title": detail_fields.get("job_title"),
        "employer": detail_fields.get("employer"),
        "category": detail_fields.get("category"),
        "location": detail_fields.get("location"),
        "work_type": detail_fields.get("work_type"),
        "career_level": detail_fields.get("career_level"),
        "employment_type": None,
        "number_required": detail_fields.get("number_required"),
        "education_required": detail_fields.get("education_required"),
        "years_experience_required": None,
        "skills_required": detail_fields.get("skills_required"),
        "special_skill_training": None,
        "salary_hint": detail_fields.get("salary_hint"),
        "application_deadline": detail_fields.get("application_deadline"),
        "how_to_apply": None,
        "source_url": BASE_URL + href,
        "source": SOURCE_NAME,
        # Afriworket's detail page gives an absolute publish date (e.g.
        # "October 1, 2026"), not a relative "N days ago" string like most
        # other sources — stored as-is rather than forced into a shape it
        # isn't.
        "date_posted_relative": detail_fields.get("date_posted_relative"),
        "date_scraped": None,  # filled in by caller
        "extraction_method": "detail_page",
        "description": description,
    }


def fetch_detail(page, href):
    page.goto(BASE_URL + href, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector("text=Job Description", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(400)
    body_text = page.inner_text("body")
    return parse_detail_text(body_text)


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
    log.append(["run_timestamp_utc", "new_rows", "running_total", "load_more_clicks", "parse_warnings"])
    for cell in log[1]:
        cell.font = Font(bold=True)
    return wb


def append_rows(wb, rows):
    ws = wb["Jobs"]
    for r in rows:
        ws.append([r.get(col) for col in COLUMNS])


def log_run(wb, new_count, total_count, clicks, warnings):
    log = wb["Run Log"]
    log.append([
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        new_count,
        total_count,
        clicks,
        warnings,
    ])


def main():
    existing_ids, wb = load_existing_ids(OUTPUT_XLSX)
    if wb is None:
        wb = new_workbook()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    new_rows = []
    parse_warnings = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        hrefs = discover_job_ids(page, existing_ids)
        new_hrefs = [h for h in hrefs if job_id_from_href(h) not in existing_ids]

        for href in new_hrefs:
            job_id = job_id_from_href(href)
            try:
                detail_fields = fetch_detail(page, href)
            except Exception as e:
                print(f"  [warn] failed to fetch detail for {job_id}: {e}", file=sys.stderr)
                continue
            if detail_fields.get("_parse_warning"):
                parse_warnings += 1
                print(f"  [warn] unexpected detail shape for {job_id}", file=sys.stderr)
                continue
            fields = structure_job(job_id, href, detail_fields)
            fields["date_scraped"] = now_str
            new_rows.append(fields)
            existing_ids.add(job_id)

        browser.close()

    if new_rows:
        append_rows(wb, new_rows)

    total = wb["Jobs"].max_row - 1
    log_run(wb, len(new_rows), total, len(hrefs), parse_warnings)
    wb.save(OUTPUT_XLSX)
    print(f"Discovered {len(hrefs)} listing hrefs, {len(new_rows)} new postings "
          f"({parse_warnings} parse warnings), {total} total in {OUTPUT_XLSX}.")


if __name__ == "__main__":
    main()
