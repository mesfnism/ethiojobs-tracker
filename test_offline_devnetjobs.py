"""
Offline tests for devnetjobs_pipeline.py's parsers, against HTML/text
fragments built to match the real live DOM structure inspected via a
browser on devnetjobs.org's search-results and job-description pages
(span ids/classes, the "(Value Members only)" locked marker, the
Job ID/Relevant Sectors/HOW TO APPLY label layout) — this sandbox has no
live network access to the site itself, so this is the same kind of
structural fixture used for reporterjobs_pipeline.py and
test_offline_palmjobs.py, not a captured live HTTP response.
"""

from devnetjobs_pipeline import (
    extract_viewstate,
    parse_results_rows,
    strip_tags_to_text,
    parse_detail_text,
    structure_job,
    LOCKED_MARKER,
)


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


# A results-grid page fragment matching the real structure observed live:
# one open posting, one "(Value Members only)" locked posting.
RESULTS_HTML = """
<html><body>
<form>
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="VS_PAGE1_ABC123" />
<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="VG_XYZ" />
<div class="jobResultSeparator">
  <div class="row mt-3 mb-3">
    <div class="col-12">
      <a id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl03_lnkJobTitle" class="text-dark"
         href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$SearchView1$grdJobs$ctl03$lnkJobTitle','')">
        <p class="fw-bold jobResultTitle mb-1">
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl03_lblJobTitle">Protection Assistant - Monitoring</span>
        </p>
        <p class="m-0 jobResultSubInfo">
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl03_lblCompany">Danish Refugee Council</span>
        </p>
        <p class="m-0 jobResultSubInfo">Location:
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl03_lblLocation">Mai-Tsebri, Ethiopia</span>
        </p>
        <p class="m-0 jobResultSubInfo float-end">Apply by:
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl03_lblPostedon">04 Oct 2026</span>
        </p>
      </a>
    </div>
  </div>
</div>
<div class="jobResultSeparator">
  <div class="row mt-3 mb-3">
    <div class="col-12">
      <a id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl04_lnkJobTitle" class="text-dark"
         href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$SearchView1$grdJobs$ctl04$lnkJobTitle','')">
        <p class="fw-bold jobResultTitle mb-1">
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl04_lblJobTitle">MEAL Officer</span>
        </p>
        <p class="m-0 jobResultSubInfo">
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl04_lblCompany">(Value Members only)</span>
        </p>
        <p class="m-0 jobResultSubInfo">Location:
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl04_lblLocation">Regional / Global</span>
        </p>
        <p class="m-0 jobResultSubInfo float-end">Apply by:
          <span id="ctl00_ContentPlaceHolder1_SearchView1_grdJobs_ctl04_lblPostedon">13 Oct 2026</span>
        </p>
      </a>
    </div>
  </div>
</div>
</form>
</body></html>
"""

# A jobdescription.aspx page's rendered body, matching the real captured
# text from job_id=316197 (Protection Assistant - Monitoring).
DETAIL_HTML = """
<html><body>
<p>Job ID: 316197</p>
<h1>Protection Assistant - Monitoring</h1>
<p>Danish Refugee Council</p>
<p>Location: Mai-Tsebri, Ethiopia</p>
<p>Apply by: 04 Oct 2026</p>
<div>Relevant Sectors</div>
<p>Administration, Management, Finance/Accounting, Procurement</p>
<p>Danish Refugee Council is looking for a qualified Protection Assistant
for operations in Mai Tsebri, Ethiopia. Requires minimum one year's
experience in humanitarian response and refugee/IDP protection.</p>
<p>Closing date for applications: October 04, 2026</p>
<p>HOW TO APPLY:</p>
<p>All applicants must upload a cover letter and an updated CV via the
online application link.
https://candidate.hr-manager.net/ApplicationInit.aspx?cid=1036</p>
<p>View Similar Jobs:</p>
<p>Was this job of interest to you? Subscribe to Value Membership.</p>
</body></html>
"""


def main():
    all_ok = True

    # --- extract_viewstate --------------------------------------------------
    vs, vg = extract_viewstate(RESULTS_HTML)
    all_ok &= check("viewstate extracted", vs == "VS_PAGE1_ABC123")
    all_ok &= check("viewstategenerator extracted", vg == "VG_XYZ")
    all_ok &= check("missing viewstate doesn't crash", extract_viewstate("<html></html>") == ("", ""))

    # --- parse_results_rows --------------------------------------------------
    rows = parse_results_rows(RESULTS_HTML)
    all_ok &= check("two rows parsed", len(rows) == 2)

    open_row = next((r for r in rows if r["ctl_id"] == "ctl03"), None)
    locked_row = next((r for r in rows if r["ctl_id"] == "ctl04"), None)
    all_ok &= check("open row found", open_row is not None)
    all_ok &= check("locked row found", locked_row is not None)

    all_ok &= check("open row: title", open_row["job_title"] == "Protection Assistant - Monitoring")
    all_ok &= check("open row: employer", open_row["employer"] == "Danish Refugee Council")
    all_ok &= check("open row: location", open_row["location"] == "Mai-Tsebri, Ethiopia")
    all_ok &= check("open row: apply-by carried as deadline", open_row["application_deadline"] == "04 Oct 2026")
    all_ok &= check("open row: not locked", open_row["locked"] is False)

    all_ok &= check("locked row: detected via marker text", locked_row["locked"] is True)
    all_ok &= check("locked row: employer is None (never exposed)", locked_row["employer"] is None)
    all_ok &= check("locked row: title still captured", locked_row["job_title"] == "MEAL Officer")
    all_ok &= check("LOCKED_MARKER constant matches fixture text", locked_row is not None and LOCKED_MARKER == "(Value Members only)")

    all_ok &= check("no rows on empty page", parse_results_rows("<html></html>") == [])

    # --- strip_tags_to_text ---------------------------------------------------
    text = strip_tags_to_text(DETAIL_HTML)
    all_ok &= check("tags stripped, no angle brackets left", "<" not in text and ">" not in text)
    all_ok &= check("text content preserved", "Protection Assistant - Monitoring" in text)
    all_ok &= check("HOW TO APPLY label preserved", "HOW TO APPLY:" in text)

    # --- parse_detail_text ------------------------------------------------
    detail = parse_detail_text(text)
    all_ok &= check("detail: location", detail["location"] == "Mai-Tsebri, Ethiopia")
    all_ok &= check("detail: category/sectors", detail["category"] == "Administration, Management, Finance/Accounting, Procurement")
    all_ok &= check("detail: deadline prefers 'Closing date'", detail["application_deadline"] == "October 04, 2026")
    all_ok &= check(
        "detail: description captured (not sectors line, not closing-date line)",
        detail["description"] is not None
        and "Protection Assistant" in detail["description"]
        and "Relevant Sectors" not in detail["description"]
        and "Closing date" not in detail["description"],
    )
    all_ok &= check(
        "detail: how_to_apply captured, stops before 'View Similar Jobs'",
        detail["how_to_apply"] is not None
        and "cover letter" in detail["how_to_apply"]
        and "View Similar Jobs" not in detail["how_to_apply"],
    )

    # --- parse_detail_text: degenerate input shouldn't crash -----------------
    empty_detail = parse_detail_text("")
    all_ok &= check("empty detail text: no crash, all None", all(v is None for v in empty_detail.values()))

    # --- structure_job ------------------------------------------------------
    structured = structure_job(open_row, "316197", detail)
    all_ok &= check("structured: job_id", structured["job_id"] == "316197")
    all_ok &= check("structured: job_title from row", structured["job_title"] == "Protection Assistant - Monitoring")
    all_ok &= check("structured: employer from row", structured["employer"] == "Danish Refugee Council")
    all_ok &= check("structured: location prefers detail page", structured["location"] == "Mai-Tsebri, Ethiopia")
    all_ok &= check("structured: category from detail sectors", structured["category"] is not None)
    all_ok &= check(
        "structured: deadline prefers detail's 'Closing date' over listing's 'Apply by'",
        structured["application_deadline"] == "October 04, 2026",
    )
    all_ok &= check("structured: source label", structured["source"] == "DevNetJobs")
    all_ok &= check("structured: source_url includes job_id", "job_id=316197" in structured["source_url"])
    all_ok &= check("structured: salary_hint honestly None (no salary field on this site)", structured["salary_hint"] is None)
    all_ok &= check("structured: extraction_method flagged", structured["extraction_method"] == "form_postback_replay")

    # Locked row should never be structured in practice (main() skips it
    # before ever calling resolve_job/structure_job) — this just confirms
    # structure_job itself doesn't choke if ever handed one.
    locked_structured = structure_job(locked_row, "999999", {})
    all_ok &= check("structured: locked row's employer stays None", locked_structured["employer"] is None)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
