"""
Offline tests for afriworket_pipeline.py's detail-page parser. The fixture
text below is copied, with only whitespace cleanup, from a real detail
page's extracted body text, captured live on 2026-10-01 against
afriworket.com/jobs/e0f4b40a-cae1-44ac-b208-26d7af160448 ("Site
Engineeer"), including the no-disclosed-salary case (a "Monthly" salary
type line with no preceding ETB amount).
"""

from afriworket_pipeline import (
    parse_detail_text,
    structure_job,
    job_id_from_href,
)

DETAIL_TEXT_NO_SALARY = """Back

You need to login to apply for this job

Login to Apply
Site Engineeer
Construction & Civil Engineering

Posted October 1, 2026

Jimma, Ethiopia

Job Type: Onsite - Full Time

Deadline: October 6, 2026

Vacancies: 1

Education Qualification: Bachelor's Degree

Applicants Needed: Both

Monthly

Salary Type

SENIOR

Experience Level

Skills And Expertise
Design Documentation & Specifications
Leadership
Communication
Work Address
Jimma,Agaro
Job Description

Key Responsibilities (Site Engineer)
Oversee day-to-day construction activities.
Set out levels, grades, and elevations.

Private Client

Jobs Posted: 4
"""

DETAIL_TEXT_WITH_SALARY = """Back

Login to Apply
Graphic designer
Creative & Design

Posted October 1, 2026

Addis Ababa, Ethiopia

Job Type: Onsite - Full Time

Deadline: October 29, 2026

Vacancies: 1

Education Qualification: Not Required

Applicants Needed: Both

12,000 ETB

Monthly

Salary Type

SENIOR

Experience Level

Skills And Expertise
Photo Editing
Graphic Design

Work Address
Addis Ababa,Bole
Job Description

We are looking for a creative Graphic Designer.

DAMA Advertising

Jobs Posted: 1
"""


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    all_ok &= check(
        "job_id_from_href extracts the uuid",
        job_id_from_href("/jobs/e0f4b40a-cae1-44ac-b208-26d7af160448") == "e0f4b40a-cae1-44ac-b208-26d7af160448",
    )
    all_ok &= check("job_id_from_href leaves a non-matching href alone", job_id_from_href("/pricing") == "/pricing")

    d = parse_detail_text(DETAIL_TEXT_NO_SALARY)
    all_ok &= check("title", d["job_title"] == "Site Engineeer")
    all_ok &= check("category", d["category"] == "Construction & Civil Engineering")
    all_ok &= check("location", d["location"] == "Jimma, Ethiopia")
    all_ok &= check("work_type (job site + schedule combined)", d["work_type"] == "Onsite - Full Time")
    all_ok &= check("deadline", d["application_deadline"] == "October 6, 2026")
    all_ok &= check("vacancies parsed to int", d["number_required"] == 1)
    all_ok &= check("education", d["education_required"] == "Bachelor's Degree")
    all_ok &= check("career_level", d["career_level"] == "SENIOR")
    all_ok &= check("no ETB amount -> salary_hint is None", d["salary_hint"] is None)
    all_ok &= check(
        "skills joined",
        d["skills_required"] == "Design Documentation & Specifications; Leadership; Communication",
    )
    all_ok &= check("work_address", d["work_address"] == "Jimma,Agaro")
    all_ok &= check("employer", d["employer"] == "Private Client")
    all_ok &= check("description captured", d["description"] is not None and "Oversee day-to-day construction activities" in d["description"])
    all_ok &= check("posted date (absolute, not relative)", d["date_posted_relative"] == "October 1, 2026")

    d2 = parse_detail_text(DETAIL_TEXT_WITH_SALARY)
    all_ok &= check("salary_hint combines amount and type when disclosed", d2["salary_hint"] == "12,000 ETB / Monthly")
    all_ok &= check("single skill list still joins cleanly", d2["skills_required"] == "Photo Editing; Graphic Design")
    all_ok &= check("employer differs per posting", d2["employer"] == "DAMA Advertising")

    row = structure_job("e0f4b40a-cae1-44ac-b208-26d7af160448", "/jobs/e0f4b40a-cae1-44ac-b208-26d7af160448", d)
    all_ok &= check("structured: job_id", row["job_id"] == "e0f4b40a-cae1-44ac-b208-26d7af160448")
    all_ok &= check("structured: source label", row["source"] == "Afriworket")
    all_ok &= check(
        "structured: source_url built from BASE_URL + href",
        row["source_url"] == "https://afriworket.com/jobs/e0f4b40a-cae1-44ac-b208-26d7af160448",
    )
    all_ok &= check("structured: job description kept", "Oversee day-to-day construction activities" in row["description"])
    all_ok &= check("structured: work address folded into description", "Jimma,Agaro" in row["description"])
    all_ok &= check("structured: extraction_method", row["extraction_method"] == "detail_page")

    # Degenerate input shouldn't crash the parser.
    broken = parse_detail_text("nothing useful here")
    all_ok &= check("unexpected shape is flagged rather than guessed at", broken.get("_parse_warning") is not None)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
