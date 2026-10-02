"""
Offline tests for reporterjobs_pipeline.py's parsers. The listing-card
fixture below is copied verbatim from a real card's extracted text,
captured live on 2026-10-01 by running this pipeline's own
href-filter-plus-closest('li, article, div') extraction in a real browser
against www.ethiopianreporterjobs.com, so it reflects the site's actual
field order (title, "@ Employer", location, "Published ... ago", one or
more category lines, a job-type badge) rather than an assumption about
it. The label-based detail parser doesn't depend on field order and was
already covered by a fixture built from the site's real field labels.
"""

from reporterjobs_pipeline import (
    parse_listing_card, parse_detail_text, structure_job,
    fetch_listing_page, ListingPageBlocked, _extract_cards,
)


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


# A real HTML card, built from the exact field order/markup confirmed live
# on 2026-10-02 by inspecting the real listing page in a browser (title
# link, "@ Employer" link, a plain location line, a "Published ... ago"
# line, one or more category links separated by a lone comma, then a
# job-type badge) — used to test the curl_cffi+BeautifulSoup extraction
# path end-to-end, not just parse_listing_card in isolation.
_REAL_LISTING_HTML = """
<html><body>
<ul>
<li class="jobsearch-joblisting-classic-wrap">
  <a href="https://www.ethiopianreporterjobs.com/jobs/129328/">IT and Digital Risk Officer (Re – advertized)</a>
  <a href="https://www.ethiopianreporterjobs.com/company/68218-bunna-bank/">@ Bunna Bank</a>
  <span>Addis Ababa, Ethiopia</span>
  <span>Published 1 day ago</span>
  <a href="https://www.ethiopianreporterjobs.com/banking-and-insurance-jobs/">Banking and Insurance</a>, <a href="https://www.ethiopianreporterjobs.com/it-jobs/">IT and Telecommunication</a>
  <span>FULL-TIME</span>
</li>
</ul>
</body></html>
"""


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeSession:
    """Minimal stand-in for a curl_cffi Session, covering only .get(), so
    the blocked-vs-legitimate-end distinction can be tested without a real
    network call."""

    def __init__(self, status_code, html=""):
        self._status_code = status_code
        self._html = html

    def get(self, url, headers=None, timeout=None):
        return _FakeResponse(self._status_code, self._html)


def main():
    all_ok = True

    # Listing card — real field order verified live: title, "@ Employer",
    # location, "Published ... ago", category (possibly several, joined
    # by a lone "," line), then a job-type badge as the last line.
    card_text = (
        "IT and Digital Risk Officer (Re – advertized)\n"
        "@ Bunna Bank\n"
        "Addis Ababa, Ethiopia\n"
        "Published 1 day ago\n"
        "Banking and Insurance\n"
        ",\n"
        "IT and Telecommunication\n"
        "FULL-TIME"
    )
    listing = parse_listing_card("/jobs/129328/", card_text)
    all_ok &= check("listing: job_id from href", listing["job_id"] == "129328")
    all_ok &= check("listing: title", listing["job_title"] == "IT and Digital Risk Officer (Re – advertized)")
    all_ok &= check("listing: employer", listing["employer"] == "Bunna Bank")
    all_ok &= check("listing: location", listing["location"] == "Addis Ababa, Ethiopia")
    all_ok &= check("listing: category joins multiple values",
                     listing["category"] == "Banking and Insurance, IT and Telecommunication")
    all_ok &= check("listing: job-type badge captured", listing["work_type"] == "FULL-TIME")

    # A single-category card (no lone "," line) should also parse cleanly.
    single_cat_text = (
        "Accountant\n@ S.Sara Coffee Export Enterprise\nAddis Ababa, Ethiopia\n"
        "Published 2 days ago\nAccounting, Auditing and Finance\nFULL-TIME"
    )
    single = parse_listing_card("/jobs/129076/", single_cat_text)
    all_ok &= check("listing: single category, no stray comma line",
                     single["category"] == "Accounting, Auditing and Finance")
    all_ok &= check("listing: single-category job-type badge", single["work_type"] == "FULL-TIME")

    # Detail page — label-based, order independent
    detail_text = (
        "IT and Digital Risk Officer (Re – advertized)\n"
        "Bunna Bank\n"
        "Location: Ethiopia, Wello-Sefer, 9th floor, Addis Ababa, Ethiopia ET\n"
        "Employment Type: Full-Time / Permanent\n"
        "Career Level: Mid Level (2+ - 5 years)\n"
        "Industry: Banking, Microfinance, Insurance ICT And Telecommunication\n"
        "Education Required: First Degree(Bsc) in Computer Science, software engineering, "
        "Computer Engineering, electrical engineering, information technology, or related fields\n"
        "Experience Required: minimum of 3 years of relevant experience\n"
        "Monthly Salary: As per Company Scale\n"
        "Posted Date: September 30, 2026\n"
        "Application Deadline: October 5, 2026\n"
        "How to Apply: Submit credentials via Google Form at the provided link\n"
        "Job ID: 129328\n"
    )
    detail = parse_detail_text(detail_text)
    all_ok &= check("detail: employment_type", detail["employment_type"] == "Full-Time / Permanent")
    all_ok &= check("detail: career_level", detail["career_level"] == "Mid Level (2+ - 5 years)")
    all_ok &= check("detail: education classified as Bachelor's", detail["education_required"] == "Bachelor's degree")
    all_ok &= check("detail: experience text captured", detail["years_experience_required"] is not None)
    all_ok &= check("detail: salary (unclassifiable, kept as text)", detail["salary_hint"] == "As per Company Scale")
    all_ok &= check("detail: deadline", detail["application_deadline"] == "October 5, 2026")
    all_ok &= check("detail: location", "Addis Ababa" in (detail["location"] or ""))

    # structure_job merge — detail wins where it has a value
    row = structure_job(listing, detail)
    all_ok &= check("structured: job_id", row["job_id"] == "129328")
    all_ok &= check("structured: source label", row["source"] == "ReporterJobs")
    all_ok &= check("structured: salary carried through", row["salary_hint"] == "As per Company Scale")
    all_ok &= check("structured: education carried through", row["education_required"] == "Bachelor's degree")

    # Degenerate shapes shouldn't crash the parser
    all_ok &= check("listing: empty text doesn't crash", parse_listing_card("/jobs/1/", "")["job_id"] == "1")
    all_ok &= check("detail: empty text doesn't crash", parse_detail_text("")["employment_type"] is None)

    # --- Blocked-vs-legitimate-end detection (the 2026-10-02 fix) ---
    # A 403 (or any 4xx/5xx) on page 1 must raise, never return [].
    try:
        fetch_listing_page(_FakeSession(status_code=403), 1)
        all_ok &= check("HTTP 403 on page 1 raises ListingPageBlocked", False)
    except ListingPageBlocked:
        all_ok &= check("HTTP 403 on page 1 raises ListingPageBlocked", True)

    # A 403 on a LATER page must also raise (never a legitimate end signal).
    try:
        fetch_listing_page(_FakeSession(status_code=403), 4)
        all_ok &= check("HTTP 403 on a later page also raises", False)
    except ListingPageBlocked:
        all_ok &= check("HTTP 403 on a later page also raises", True)

    # No status code at all (None) must raise too.
    try:
        fetch_listing_page(_FakeSession(status_code=None), 1)
        all_ok &= check("no status code raises ListingPageBlocked", False)
    except ListingPageBlocked:
        all_ok &= check("no status code raises ListingPageBlocked", True)

    # A 200 with zero job links on PAGE 1 specifically must raise (soft
    # block / layout change) rather than being read as "no more postings."
    try:
        fetch_listing_page(_FakeSession(status_code=200, html="<html><body>nothing here</body></html>"), 1)
        all_ok &= check("200 with zero links on page 1 raises", False)
    except ListingPageBlocked:
        all_ok &= check("200 with zero links on page 1 raises", True)

    # A 200 with zero job links on a LATER page is a legitimate end of
    # pagination and must NOT raise — it should just return [].
    try:
        result = fetch_listing_page(_FakeSession(status_code=200, html="<html><body>nothing here</body></html>"), 5)
        all_ok &= check("200 with zero links on a later page returns [] (no raise)", result == [])
    except ListingPageBlocked:
        all_ok &= check("200 with zero links on a later page returns [] (no raise)", False)

    # A normal, healthy page 1, using the real captured HTML shape, must
    # return real cards whose text is already in the exact shape
    # parse_listing_card expects — the full curl_cffi+BeautifulSoup path,
    # not just parse_listing_card in isolation.
    try:
        result = fetch_listing_page(_FakeSession(status_code=200, html=_REAL_LISTING_HTML), 1)
        all_ok &= check("200 with real HTML on page 1 returns 1 card", len(result) == 1)
        parsed = parse_listing_card(result[0]["href"], result[0]["text"]) if result else {}
        all_ok &= check("extracted card parses to the right employer", parsed.get("employer") == "Bunna Bank")
        all_ok &= check("extracted card parses to the right categories",
                         parsed.get("category") == "Banking and Insurance, IT and Telecommunication")
    except ListingPageBlocked:
        all_ok &= check("200 with real HTML on page 1 returns 1 card", False)

    # _extract_cards dedups by resolved URL even if the same job is linked
    # twice on the page (title link + a "view details" style second link).
    dup_html = _REAL_LISTING_HTML.replace(
        "</li>",
        '<a href="/jobs/129328/">View details</a></li>',
    )
    cards = _extract_cards(dup_html, "https://www.ethiopianreporterjobs.com/jobs-in-ethiopia/")
    all_ok &= check("duplicate hrefs to the same job are deduped", len(cards) == 1)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
