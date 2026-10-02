"""
GeezJobs Pipeline — companion scraper for geezjobs.com/jobs-in-ethiopia.

Unlike the other sources in this project, GeezJobs' own /jobs-in-ethiopia
page is not a paginated listing at all. It is a homepage-style page with
a fixed "Featured Vacancies" carousel and a fixed "Latest Job Vacancies"
list (roughly 45 and 30 postings respectively, confirmed live on
2026-10-01), and no further pagination, "load more" control, or visible
API call was found behind it — the in-page search box filters the already
-loaded set client-side rather than fetching more. So this pipeline reads
exactly what that one page shows: the union of both sections' postings.
That is a real, honest limit on this source's depth (see its entry in
generate_intelligence_report.py's Data Sources section), not a bug to be
engineered around, since the site itself doesn't expose anything deeper.

Each posting has its own detail page at /job-detail/<slug>, which is
where every field below is actually read from (the two-phase pattern used
throughout this project: the overview page is only for discovering
which postings exist, the detail page is the authoritative source).
"""

import re
import sys
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import sync_playwright

SOURCE_NAME = "GeezJobs"
BASE_URL = "https://geezjobs.com"
LISTING_URL = BASE_URL + "/jobs-in-ethiopia"
OUTPUT_XLSX = "geezjobs_tracker.xlsx"

COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

_DETAIL_HREF_RE = re.compile(r"^/job-detail/([^/?]+)/?$")


def job_id_from_href(href):
    m = _DETAIL_HREF_RE.match(href)
    return m.group(1) if m else href


def collect_job_hrefs(page):
    return page.eval_on_selector_all(
        'a[href^="/job-detail/"]',
        "els => [...new Set(els.map(e => e.getAttribute('href')))]",
    )


def discover_job_hrefs(page):
    page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector('a[href^="/job-detail/"]', timeout=20000)
    except Exception:
        return []
    page.wait_for_timeout(800)
    return sorted(set(collect_job_hrefs(page)))


def parse_detail_text(raw_text):
    """Parses a GeezJobs detail page's body text. Shape verified live on
    2026-10-01 against a real posting:

        BACK TO ALL OPPORTUNITIES
        <EMPLOYER NAME>
        VERIFIED                      (sometimes absent)
        <Job Title>
        <LOCATION - COUNTRY>
        <WORK TYPE>                   e.g. FULL-TIME
        <CONTRACT TYPE>               e.g. PERMANENT
        POSTED
        <relative time>
        EXPERIENCE
        <years>
        DEADLINE
        <absolute date> (<N days left>)
        Job Summary
        <summary text>
        About the Job
        <full description, including an embedded "Education & Experience
        Required for the Position" block with Education/Field of
        Study/Experience lines>
        APPLY FOR THIS JOB
    """
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]

    try:
        anchor_idx = lines.index("BACK TO ALL OPPORTUNITIES")
    except ValueError:
        return {"_parse_warning": "unexpected_detail_shape"}

    rest = lines[anchor_idx + 1:]
    if not rest:
        return {"_parse_warning": "unexpected_detail_shape"}

    employer = rest[0]
    idx = 1
    if idx < len(rest) and rest[idx] == "VERIFIED":
        idx += 1
    if idx >= len(rest):
        return {"_parse_warning": "unexpected_detail_shape"}
    title = rest[idx]
    idx += 1
    location = rest[idx] if idx < len(rest) else None
    idx += 1

    # Work type / contract type: zero or more short all-caps badge lines
    # before the next recognised label ("POSTED").
    badges = []
    while idx < len(rest) and rest[idx] not in ("POSTED",):
        if re.fullmatch(r"[A-Z][A-Z \-/]*", rest[idx]):
            badges.append(rest[idx])
            idx += 1
        else:
            break
    work_type = badges[0] if len(badges) > 0 else None
    employment_type = badges[1] if len(badges) > 1 else None

    def value_after_label(label):
        try:
            i = rest.index(label)
        except ValueError:
            return None
        return rest[i + 1] if i + 1 < len(rest) else None

    posted_relative = value_after_label("POSTED")
    years_experience = value_after_label("EXPERIENCE")
    deadline = value_after_label("DEADLINE")

    # Everything from "Job Summary" (or "About the Job" if that label is
    # missing) up to the "APPLY FOR THIS JOB" button is kept as the
    # description, including the embedded education/experience block —
    # GeezJobs doesn't expose those as separate structured fields outside
    # this free text, so they're extracted from it rather than left blank
    # when the phrasing is clear enough to trust.
    desc_start_label = "Job Summary" if "Job Summary" in rest else "About the Job"
    description = None
    if desc_start_label in rest:
        start = rest.index(desc_start_label) + 1
        end = rest.index("APPLY FOR THIS JOB") if "APPLY FOR THIS JOB" in rest else len(rest)
        description = "\n".join(rest[start:end]).strip() or None

    education = None
    if description:
        m = re.search(r"Education\s*:\s*([^\n]+)", description)
        if m:
            education = m.group(1).strip()

    return {
        "job_title": title,
        "employer": employer,
        "location": location,
        "work_type": work_type,
        "employment_type": employment_type,
        "date_posted_relative": posted_relative,
        "years_experience_required": years_experience,
        "application_deadline": deadline,
        "education_required": education,
        "description": description,
    }


def structure_job(job_id, href, detail_fields):
    return {
        "job_id": job_id,
        "job_title": detail_fields.get("job_title"),
        "employer": detail_fields.get("employer"),
        "category": None,
        "location": detail_fields.get("location"),
        "work_type": detail_fields.get("work_type"),
        "career_level": None,
        "employment_type": detail_fields.get("employment_type"),
        "number_required": None,
        "education_required": detail_fields.get("education_required"),
        "years_experience_required": detail_fields.get("years_experience_required"),
        "skills_required": None,
        "special_skill_training": None,
        "salary_hint": None,
        "application_deadline": detail_fields.get("application_deadline"),
        "how_to_apply": None,
        "source_url": BASE_URL + href,
        "source": SOURCE_NAME,
        "date_posted_relative": detail_fields.get("date_posted_relative"),
        "date_scraped": None,  # filled in by caller
        "extraction_method": "detail_page",
        "description": detail_fields.get("description"),
    }


def fetch_detail(page, href):
    page.goto(BASE_URL + href, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector("text=BACK TO ALL OPPORTUNITIES", timeout=20000)
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
    log.append(["run_timestamp_utc", "new_rows", "running_total", "listing_hrefs_seen", "parse_warnings"])
    for cell in log[1]:
        cell.font = Font(bold=True)
    return wb


def append_rows(wb, rows):
    ws = wb["Jobs"]
    for r in rows:
        ws.append([r.get(col) for col in COLUMNS])


def log_run(wb, new_count, total_count, hrefs_seen, warnings):
    log = wb["Run Log"]
    log.append([
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        new_count,
        total_count,
        hrefs_seen,
        warnings,
    ])


def main():
    existing_ids, wb = load_existing_ids(OUTPUT_XLSX)
    if wb is None:
        wb = new_workbook()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    new_rows = []
    parse_warnings = 0
    hrefs = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        hrefs = discover_job_hrefs(page)
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
    print(f"Saw {len(hrefs)} listing hrefs, {len(new_rows)} new postings "
          f"({parse_warnings} parse warnings), {total} total in {OUTPUT_XLSX}.")


if __name__ == "__main__":
    main()
