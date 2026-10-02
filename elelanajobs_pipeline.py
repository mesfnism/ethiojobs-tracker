"""
ElelanaJobs Pipeline — companion scraper for elelanajobs.com, a WordPress
site built on the WP Job Manager plugin.

Unlike the other sources, this one is not scraped from its HTML listing
pages at all. WP Job Manager exposes every published job_listing post
through a standard WordPress RSS feed at /?feed=job_feed, and that feed
already carries, per item, the posting's title, permalink, publish date,
a coarse job_listing:location field, and the FULL job description as
content:encoded HTML. That is strictly more than the listing page's own
cards show (which are just title, "Ethiopia", and a relative "Posted ...
ago" line), so the feed is both the simpler and the richer source. The
feed paginates the normal WordPress way, with &paged=N, and an empty page
(0 <item> elements) marks the end — verified live on 2026-10-01 by paging
through to the first empty page.

A real limitation worth stating plainly rather than working around: many
items on this site are not single-role postings. A single item titled,
say, "Hibret Bank SC" is often a bulletin announcing several distinct
openings at that employer in one post. There is no structural way to
split those back into individual postings from the feed (or the site)
without guessing, so each feed item is kept as one row, exactly as the
site itself structures it. job_title and employer are therefore often
identical (the bulletin's own headline), and per-role fields such as
education or experience are left None rather than attributed to the
wrong role within a multi-role bulletin.

The feed is fetched with Playwright for consistency with the rest of this
project's pipelines (and in case the site ever adds bot protection a
plain HTTP client would trip), but critically through the Response
object's own .text() method rather than by reading the rendered DOM —
Chromium's built-in XML pretty-printer repaints the page and would
otherwise interfere with getting the exact raw feed body.

Some feed items are not vacancies at all but result or interview-call
announcements (e.g. "Call For Language Proficiency Assessment"). These
are recognisable by their titles and are excluded from the tracker
entirely, consistent with this project's definition of what counts as a
posting (see generate_intelligence_report.py's "what this report
measures" note) — an announcement that nobody can apply to is not a
vacancy.
"""

import html
import re
import sys
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import sync_playwright

SOURCE_NAME = "ElelanaJobs"
BASE_URL = "https://elelanajobs.com"
FEED_URL_TEMPLATE = BASE_URL + "/?feed=job_feed&paged={page}"
OUTPUT_XLSX = "elelanajobs_tracker.xlsx"
MAX_PAGES = 40  # safety cap; the real run stops on the first empty page

# Columns match ethiojobs_tracker.xlsx exactly, so combine_trackers.py can
# simply concatenate rows rather than map between schemas.
COLUMNS = [
    "job_id", "job_title", "employer", "category", "location", "work_type",
    "career_level", "employment_type", "number_required",
    "education_required", "years_experience_required", "skills_required",
    "special_skill_training", "salary_hint", "application_deadline",
    "how_to_apply", "source_url", "source", "date_posted_relative",
    "date_scraped", "extraction_method", "description",
]

# Titles matching any of these are result/interview/assessment calls, not
# postings a candidate can apply to, and are dropped rather than counted.
_NOISE_TITLE_RE = re.compile(
    r"\b(call for|result announcement|written exam|interview|shortlist|"
    r"assessment|exam result)\b",
    re.I,
)

_DEADLINE_RE = re.compile(
    r"(?:application\s+)?deadline\s*:\s*([^<\n]{3,60})",
    re.I,
)

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE_RE = re.compile(r"<title>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</title>", re.S)
_LINK_RE = re.compile(r"<link>(.*?)</link>", re.S)
_PUBDATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
_LOCATION_RE = re.compile(r"<job_listing:location><!\[CDATA\[(.*?)\]\]></job_listing:location>", re.S)
_CONTENT_RE = re.compile(r"<content:encoded><!\[CDATA\[(.*?)\]\]></content:encoded>", re.S)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def html_to_text(raw_html):
    if not raw_html:
        return None
    text = raw_html.replace("<br />", "\n").replace("<br/>", "\n").replace("</p>", "\n\n")
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip() or None


def job_id_from_link(link):
    # https://elelanajobs.com/job/hibret-bank-sc-oct-1-26/ -> hibret-bank-sc-oct-1-26
    m = re.search(r"/job/([^/]+)/?$", link)
    return m.group(1) if m else link


def extract_deadline(description_text):
    if not description_text:
        return None
    m = _DEADLINE_RE.search(description_text)
    return m.group(1).strip().rstrip(".") if m else None


def parse_feed_items(xml_text):
    """Parses one feed page's raw XML text into a list of item dicts.
    Returns [] for a page with no <item> elements (the end-of-feed
    signal)."""
    items = []
    for raw in _ITEM_RE.findall(xml_text):
        title_match = _TITLE_RE.search(raw)
        title = None
        if title_match:
            title = (title_match.group(1) or title_match.group(2) or "").strip()
            title = html.unescape(title)
        link_match = _LINK_RE.search(raw)
        link = link_match.group(1).strip() if link_match else None
        pubdate_match = _PUBDATE_RE.search(raw)
        pubdate = pubdate_match.group(1).strip() if pubdate_match else None
        location_match = _LOCATION_RE.search(raw)
        location = location_match.group(1).strip() if location_match else None
        content_match = _CONTENT_RE.search(raw)
        content_html = content_match.group(1) if content_match else None

        if not (title and link):
            continue
        items.append({
            "title": title,
            "link": link,
            "pubdate": pubdate,
            "location": location,
            "content_html": content_html,
        })
    return items


def structure_item(item):
    description = html_to_text(item["content_html"])
    job_id = job_id_from_link(item["link"])
    return {
        "job_id": job_id,
        "job_title": item["title"],
        # The feed carries no field separate from the bulletin's own
        # headline, which is usually the hiring employer's name (see the
        # module docstring) — kept honest rather than invented.
        "employer": item["title"],
        "category": None,
        "location": item["location"] or "Ethiopia",
        "work_type": None,
        "career_level": None,
        "employment_type": None,
        "number_required": None,
        "education_required": None,
        "years_experience_required": None,
        "skills_required": None,
        "special_skill_training": None,
        "salary_hint": None,
        "application_deadline": extract_deadline(description),
        "how_to_apply": None,
        "source_url": item["link"],
        "source": SOURCE_NAME,
        # This feed gives an absolute publish timestamp, not a relative
        # "N days ago" string like most other sources — stored as-is
        # rather than forced into a shape it isn't.
        "date_posted_relative": item["pubdate"],
        "date_scraped": None,  # filled in by caller
        "extraction_method": "rss_feed",
        "description": description,
    }


def is_noise_item(title):
    return bool(_NOISE_TITLE_RE.search(title or ""))


def fetch_feed_page(page, page_num):
    url = FEED_URL_TEMPLATE.format(page=page_num)
    response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if response is None:
        return ""
    # .text() reads the raw HTTP response body — unaffected by Chromium's
    # XML pretty-printer, which would otherwise repaint the DOM and make
    # document.body.innerText useless for getting the exact feed text.
    return response.text()


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
    log.append(["run_timestamp_utc", "new_rows", "running_total", "pages_walked", "noise_skipped"])
    for cell in log[1]:
        cell.font = Font(bold=True)
    return wb


def append_rows(wb, rows):
    ws = wb["Jobs"]
    for r in rows:
        ws.append([r.get(col) for col in COLUMNS])


def log_run(wb, new_count, total_count, pages_walked, noise_skipped):
    log = wb["Run Log"]
    log.append([
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        new_count,
        total_count,
        pages_walked,
        noise_skipped,
    ])


def main():
    existing_ids, wb = load_existing_ids(OUTPUT_XLSX)
    if wb is None:
        wb = new_workbook()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    new_rows = []
    pages_walked = 0
    noise_skipped = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for page_num in range(1, MAX_PAGES + 1):
            xml_text = fetch_feed_page(page, page_num)
            pages_walked = page_num
            items = parse_feed_items(xml_text)
            if not items:
                break
            for item in items:
                if is_noise_item(item["title"]):
                    noise_skipped += 1
                    continue
                fields = structure_item(item)
                if fields["job_id"] in existing_ids:
                    continue
                fields["date_scraped"] = now_str
                new_rows.append(fields)
                existing_ids.add(fields["job_id"])
        browser.close()

    if new_rows:
        append_rows(wb, new_rows)

    total = wb["Jobs"].max_row - 1
    log_run(wb, len(new_rows), total, pages_walked, noise_skipped)
    wb.save(OUTPUT_XLSX)
    print(f"Walked {pages_walked} feed pages, {len(new_rows)} new postings "
          f"({noise_skipped} non-vacancy announcements skipped), "
          f"{total} total in {OUTPUT_XLSX}.")


if __name__ == "__main__":
    main()
