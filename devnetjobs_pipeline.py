"""
DevNetJobs Pipeline — fifth job board (devnetjobs.org), scoped to Ethiopia.

devnetjobs.org is a global international-development/NGO job board (a
classic ASP.NET WebForms site) with ~800 active postings worldwide at any
time, almost none of them Ethiopia-specific. Rather than crawl every page
of a global board, this pipeline replays the site's own "Search Jobs"
keyword search for "Ethiopia" — the exact request a browser sends when a
person types "Ethiopia" into the search box and clicks Search — which
narrows ~800 global postings down to the much smaller set that actually
mention Ethiopia (confirmed live: 29 matches across 2 result pages on the
day this was built).

Why a real browser page, not a raw POST/GET replay: result rows are
rendered as ASP.NET __doPostBack() links, not plain <a href> URLs — the
job_id is never present in the listing HTML, only resolved server-side
when that specific row's postback is submitted (confirmed by inspecting
the live DOM: no job_id in any attribute of a result row). This
pipeline originally hand-built that postback's form body with a
lightweight HTTP request context (no browser). That stopped working
without any visible site redesign: a hand-built POST carrying the exact
same fields a real browser sends still gets an HTTP 200 back but never
the redirect to jobdescription.aspx, while calling the site's own
__doPostBack() from inside a real page, with the same arguments,
redirects correctly every time (confirmed live, side by side). The page
loads ASP.NET AJAX's Sys.WebForms framework, so there is apparently
something its client code adds before a postback that a hand-built
request doesn't reproduce. Rather than keep reverse-engineering the
wire format, this pipeline now drives the whole search-and-resolve flow
through a real page, the same way reporterjobs_pipeline.py and
palmjobs_pipeline.py already do for their own JS-dependent sites, and
lets the site's own JS do the postback. The __EVENTTARGET naming
convention is still exactly what was observed live:
"ctl00$ContentPlaceHolder1$SearchView1$grdJobs$ctl0N$lnkJobTitle".

Paywalled postings: a portion of results are labelled "(Value Members
only)" in the listing itself (that text is literally what the site puts
in the employer-name slot for a locked row) — these are recognized from
the listing text alone and skipped before any further request is made.
This pipeline never attempts to log in, subscribe, or otherwise access
member-only content, consistent with the rest of this project.

Honesty about parser coverage: the detail-page parser (parse_detail_text)
is label-based (looks for "Relevant Sectors", "Closing date for
applications:", "HOW TO APPLY:", etc.) built from one real captured
posting (job_id=316197, "Protection Assistant - Monitoring", Danish
Refugee Council) — the same honesty caveat already applied to
reporterjobs_pipeline.py: it should hold up across most postings since
it's label-based rather than positional, but a first live run is the
real test, and any mismatch is a quick, isolated fix.

No salary field is exposed anywhere on devnetjobs.org postings observed
so far, so salary_hint is always None here (an honest gap, not a guess).
Likewise there's no structured education/experience/skills field — those
stay None, with the free-text description still available for the
taxonomy module's specialization extraction, same as every other source.
"""

import html as html_module
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

BASE_URL = "https://devnetjobs.org"
SEARCH_FORM_URL = f"{BASE_URL}/search_jobs.aspx"
RESULTS_URL = f"{BASE_URL}/PublicSearchView.aspx"
GRID_EVENT_TARGET = "ctl00$ContentPlaceHolder1$SearchView1$grdJobs"
ROW_ID_PREFIX = "ctl00_ContentPlaceHolder1_SearchView1_grdJobs_"

SEARCH_KEYWORD = os.environ.get("DEVNETJOBS_KEYWORD", "Ethiopia")
MAX_RESULT_PAGES = int(os.environ.get("DEVNETJOBS_MAX_RESULT_PAGES", "10"))
ROW_REQUEST_DELAY_SECONDS = float(os.environ.get("DEVNETJOBS_ROW_DELAY", "1.0"))

OUTPUT_XLSX = os.environ.get("DEVNETJOBS_OUTPUT_XLSX", "devnetjobs_tracker.xlsx")

COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

NAVY = "1F3B57"
LOCKED_MARKER = "(Value Members only)"

VIEWSTATE_RE = re.compile(r'id="__VIEWSTATE"\s+value="([^"]*)"')
VIEWSTATEGEN_RE = re.compile(r'id="__VIEWSTATEGENERATOR"\s+value="([^"]*)"')

ROW_RE = re.compile(
    r'id="' + re.escape(ROW_ID_PREFIX) + r'(ctl\d+)_lblJobTitle"[^>]*>([^<]*)</span>'
    r'.*?id="' + re.escape(ROW_ID_PREFIX) + r'\1_lblCompany"[^>]*>([^<]*)</span>'
    r'.*?id="' + re.escape(ROW_ID_PREFIX) + r'\1_lblLocation"[^>]*>([^<]*)</span>'
    r'.*?id="' + re.escape(ROW_ID_PREFIX) + r'\1_lblPostedon"[^>]*>([^<]*)</span>',
    re.S,
)

JOB_ID_IN_URL_RE = re.compile(r"job_id=(\d+)")

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_TAG_RE = re.compile(r"</(p|div|tr|li|h\d)\s*>|<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"[ \t]*\n[ \t]*")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def extract_viewstate(page_html):
    vs = VIEWSTATE_RE.search(page_html)
    vg = VIEWSTATEGEN_RE.search(page_html)
    return (vs.group(1) if vs else ""), (vg.group(1) if vg else "")


def parse_results_rows(page_html):
    """Parses one results-grid page into row dicts. Each row carries its
    own ctl_id (needed to replay that specific row's postback later) and
    a locked flag (listing text IS "(Value Members only)" in the employer
    slot for paywalled postings — detected here, before any further
    request is made, so a locked posting is never fetched)."""
    rows = []
    for m in ROW_RE.finditer(page_html):
        ctl_id, title, company, location, posted_on = m.groups()
        title = html_module.unescape(title).strip()
        company = html_module.unescape(company).strip()
        location = html_module.unescape(location).strip()
        posted_on = html_module.unescape(posted_on).strip()
        locked = company == LOCKED_MARKER
        rows.append({
            "ctl_id": ctl_id,
            "job_title": title,
            "employer": None if locked else (company or None),
            "location": location or None,
            "application_deadline": posted_on or None,
            "locked": locked,
        })
    return rows


def strip_tags_to_text(page_html):
    """Converts an HTML page to plain text with block-level line breaks
    preserved, so the label-based detail parser below can work the same
    way it would against a real browser's rendered text."""
    text = _SCRIPT_STYLE_RE.sub(" ", page_html)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text)
    text = _BLANK_LINES_RE.sub("\n", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def parse_detail_text(text):
    """Label-based parse of a jobdescription.aspx page's plain text. See
    the module docstring for the honesty caveat on this parser's coverage
    — built from one real captured posting, label-based so it should
    generalize, but unverified beyond that one example."""

    def grab(pattern, flags=re.I):
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else None

    location = grab(r"Location:\s*([^\n]+)")
    sectors = grab(r"Relevant Sectors\s*\n+([^\n]+)")
    deadline = grab(r"Closing date for applications:\s*([^\n]+)") or grab(r"Apply by:\s*([^\n]+)")
    how_to_apply = grab(
        r"HOW TO APPLY:\s*\n+(.+?)(?:\nView Similar Jobs|\nWas this job|\nSubscribe to Value Membership|\Z)",
        re.I | re.S,
    )
    description = grab(
        r"Relevant Sectors\s*\n+[^\n]+\n+(.+?)"
        r"(?:\nClosing date for applications|\nHOW TO APPLY|\Z)",
        re.I | re.S,
    )
    if description:
        description = re.sub(r"\s+", " ", description).strip()

    return {
        "location": location,
        "category": sectors,
        "application_deadline": deadline,
        "how_to_apply": how_to_apply,
        "description": description,
    }


def structure_job(row, job_id, detail):
    return {
        "job_id": job_id,
        "job_title": row["job_title"],
        "employer": row["employer"],
        "category": detail.get("category"),
        "location": detail.get("location") or row["location"],
        "work_type": None,
        "career_level": None,
        "employment_type": None,
        "number_required": None,
        "education_required": None,
        "years_experience_required": None,
        "skills_required": None,
        "special_skill_training": None,
        "salary_hint": None,  # honest gap — no salary field observed anywhere on this site
        "application_deadline": detail.get("application_deadline") or row["application_deadline"],
        "how_to_apply": detail.get("how_to_apply"),
        "source_url": f"{BASE_URL}/jobdescription.aspx?job_id={job_id}",
        "source": "DevNetJobs",
        "date_posted_relative": None,
        "date_scraped": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "extraction_method": "form_postback_replay",
        "description": detail.get("description"),
    }


# --------------------------------------------------------------------------
# Browser-driven fetch — a real page, not a raw POST/GET replay.
#
# This pipeline originally used Playwright's lightweight request context
# (no browser) for the whole flow, hand-building the __doPostBack form
# body for each row's postback. That stopped working: confirmed live, a
# hand-built POST carrying the exact same fields a real browser sends
# (checked by reading the live form's own field list) returns HTTP 200
# but never redirects to jobdescription.aspx — while calling the site's
# own __doPostBack() function from inside a real page, with the same
# arguments, redirects correctly every time. Whatever the server expects
# beyond the posted fields (likely something ASP.NET's AJAX framework
# adds client-side before submitting, since Sys.WebForms is loaded on
# this page), it's something only the page's own JS produces — so this
# now drives the whole search-and-resolve flow through a real page,
# letting the site's own client code do the postback, the same way
# reporterjobs_pipeline.py and palmjobs_pipeline.py already do for their
# own JS-dependent sites.
# --------------------------------------------------------------------------

def resolve_job(page, row):
    """Navigates back to the results listing (if needed), then calls the
    site's own __doPostBack() for this one row from inside the live page
    and follows the resulting navigation straight to its
    jobdescription.aspx?job_id=N page, returning (job_id, detail_text)."""
    event_target = f"{GRID_EVENT_TARGET}${row['ctl_id']}$lnkJobTitle"
    with page.expect_navigation(timeout=30000):
        page.evaluate(f"__doPostBack('{event_target}', '')")
    m = JOB_ID_IN_URL_RE.search(page.url)
    if not m:
        return None, None
    detail_text = page.inner_text("body")
    return m.group(1), detail_text


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
    total_found = 0
    total_locked = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        try:
            page.goto(SEARCH_FORM_URL, wait_until="domcontentloaded", timeout=45000)
            page.fill('[name="ctl00$ContentPlaceHolder1$txtKeywords"]', SEARCH_KEYWORD)
            page.click('[name="ctl00$ContentPlaceHolder1$btnSearchJob"]')
            page.wait_for_load_state("domcontentloaded", timeout=45000)
            page.wait_for_timeout(800)
        except Exception as e:
            print(f"  Keyword search failed ({e}) — stopping without writing anything.")
            browser.close()
            append_rows(output_path, [])
            return

        # Resolved page by page, rather than collecting every row first —
        # a row's ctl_id (e.g. "ctl03") is only unique within the results
        # page it was rendered on, so every row on a page must be resolved
        # (navigated to, read, navigated back from) before paginating away
        # from that page, not after.
        page_num = 1
        while True:
            try:
                html = page.content()
            except Exception as e:
                print(f"  Page {page_num}: could not read results ({e}) — stopping.")
                break
            rows = parse_results_rows(html)
            if not rows:
                break

            locked_count = sum(1 for r in rows if r["locked"])
            total_found += len(rows)
            total_locked += locked_count
            print(f"  Page {page_num}: {len(rows)} result(s), {locked_count} paywalled "
                  f"'Value Members only' — skipped.")

            for row in rows:
                if row["locked"]:
                    continue
                try:
                    job_id, detail_text = resolve_job(page, row)
                except Exception as e:
                    print(f"  '{row['job_title']}': failed to resolve ({e}) — skipping.")
                    job_id, detail_text = None, None

                if job_id and job_id not in existing_ids:
                    detail_fields = parse_detail_text(detail_text) if detail_text else {}
                    new_rows.append(structure_job(row, job_id, detail_fields))
                    existing_ids.add(job_id)
                elif not job_id:
                    print(f"  '{row['job_title']}': could not resolve a job_id — skipping.")

                # Back to the results listing (same page) for the next row,
                # whether this one succeeded, was already seen, or failed.
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(500)
                except Exception as e:
                    print(f"  Could not return to the results listing ({e}) — "
                          f"stopping pagination early.")
                    rows = []  # forces the outer while loop to stop below
                    break
                time.sleep(ROW_REQUEST_DELAY_SECONDS)

            if not rows or page_num >= MAX_RESULT_PAGES:
                break
            next_page_num = page_num + 1
            try:
                page.evaluate(
                    f"__doPostBack('{GRID_EVENT_TARGET}', 'Page${next_page_num}')"
                )
            except Exception:
                break
            page.wait_for_timeout(1200)
            next_html = page.content()
            next_rows = parse_results_rows(next_html)
            if not next_rows or next_rows == rows:
                break
            page_num = next_page_num

        browser.close()

    print(f"Found {total_found} result(s) for keyword '{SEARCH_KEYWORD}' total "
          f"({total_locked} paywalled, {len(new_rows)} new).")

    if new_rows:
        append_rows(output_path, new_rows)
        print(f"Added {len(new_rows)} new postings. Workbook: {output_path}")
    else:
        append_rows(output_path, [])
        print("No new postings this run.")


if __name__ == "__main__":
    main()
