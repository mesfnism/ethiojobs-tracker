"""
Offline tests for geezjobs_pipeline.py's detail-page parser. The fixture
text below is copied, with only whitespace cleanup, from a real detail
page's extracted body text, captured live on 2026-10-01 against
geezjobs.com/job-detail/senior-export-documentation-officer-abbahawa-trading-plc.
"""

from geezjobs_pipeline import (
    parse_detail_text,
    structure_job,
    job_id_from_href,
)

DETAIL_TEXT = """BACK TO ALL OPPORTUNITIES

ABBAHAWA TRADING PLC

VERIFIED
Senior Export Documentation Officer
ADDIS ABABA - ETHIOPIA
FULL-TIME
PERMANENT

POSTED

4 hours ago

EXPERIENCE

4 Years

DEADLINE

Oct. 10, 2026 (9 days left)

Job Summary

Abbahawa Trading PLC is inviting qualified applicants for the position of Senior Export Documentation Officer.

About the Job

Our Company Abbahawa Trading PLC needs to invite applicants whom fulfill the requirements for under listed positions.

Education & Experience Required for the Position

Education: BA Degree
Field of Study: Business Management, Business Administration, Supply Chain Management, Logistics, and related fields
Experience: 4 years of related experience
APPLY FOR THIS JOB

QUICK ACTIONS

SAVE
PRINT
REPORT
"""

DETAIL_TEXT_NO_VERIFIED = """BACK TO ALL OPPORTUNITIES

Some Employer PLC

Junior Accountant
ADDIS ABABA - ETHIOPIA
FULL-TIME
PERMANENT

POSTED

1 days ago

EXPERIENCE

0 Years

DEADLINE

Oct. 6, 2026 (5 days left)

Job Summary

A short summary.

About the Job

Full description text goes here.
APPLY FOR THIS JOB
"""


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    all_ok &= check(
        "job_id_from_href extracts the slug",
        job_id_from_href("/job-detail/senior-export-documentation-officer-abbahawa-trading-plc")
        == "senior-export-documentation-officer-abbahawa-trading-plc",
    )
    all_ok &= check("job_id_from_href leaves a non-matching href alone", job_id_from_href("/pricing") == "/pricing")

    d = parse_detail_text(DETAIL_TEXT)
    all_ok &= check("employer", d["employer"] == "ABBAHAWA TRADING PLC")
    all_ok &= check("title (after VERIFIED badge)", d["job_title"] == "Senior Export Documentation Officer")
    all_ok &= check("location", d["location"] == "ADDIS ABABA - ETHIOPIA")
    all_ok &= check("work_type", d["work_type"] == "FULL-TIME")
    all_ok &= check("employment_type", d["employment_type"] == "PERMANENT")
    all_ok &= check("posted (relative)", d["date_posted_relative"] == "4 hours ago")
    all_ok &= check("experience", d["years_experience_required"] == "4 Years")
    all_ok &= check("deadline", d["application_deadline"] == "Oct. 10, 2026 (9 days left)")
    all_ok &= check("description captured", "invite applicants" in d["description"])
    all_ok &= check(
        "education extracted from the embedded Education/Experience block",
        d["education_required"] == "BA Degree",
    )

    d2 = parse_detail_text(DETAIL_TEXT_NO_VERIFIED)
    all_ok &= check("title parses fine without a VERIFIED badge", d2["job_title"] == "Junior Accountant")
    all_ok &= check("employer without VERIFIED badge", d2["employer"] == "Some Employer PLC")
    all_ok &= check("description without an education block stays None", d2["education_required"] is None)

    row = structure_job(
        "senior-export-documentation-officer-abbahawa-trading-plc",
        "/job-detail/senior-export-documentation-officer-abbahawa-trading-plc",
        d,
    )
    all_ok &= check("structured: job_id", row["job_id"] == "senior-export-documentation-officer-abbahawa-trading-plc")
    all_ok &= check("structured: source label", row["source"] == "GeezJobs")
    all_ok &= check(
        "structured: source_url",
        row["source_url"]
        == "https://geezjobs.com/job-detail/senior-export-documentation-officer-abbahawa-trading-plc",
    )
    all_ok &= check("structured: extraction_method", row["extraction_method"] == "detail_page")

    broken = parse_detail_text("nothing useful here")
    all_ok &= check("unexpected shape is flagged rather than guessed at", broken.get("_parse_warning") is not None)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
