"""
Offline tests for reporterjobs_pipeline.py's parsers, against text
constructed from the site's actual field labels and values (observed by
inspecting the live site's rendered text — not a captured Playwright DOM
fixture, since this sandbox has no live network access to the site
itself). The label-based detail parser is the one that matters most here
(it doesn't depend on exact field order); the listing-card parser is
best-effort and documented as such.
"""

from reporterjobs_pipeline import parse_listing_card, parse_detail_text, structure_job


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    # Listing card — positional, around the "... ago" anchor
    card_text = "Banking and Insurance\nIT and Telecommunication\nIT and Digital Risk Officer (Re – advertized)\nPublished 1 day ago\nBunna Bank\nAddis Ababa, Ethiopia"
    listing = parse_listing_card("/jobs/129328/", card_text)
    all_ok &= check("listing: job_id from href", listing["job_id"] == "129328")
    all_ok &= check("listing: title", listing["job_title"] == "IT and Digital Risk Officer (Re – advertized)")
    all_ok &= check("listing: employer", listing["employer"] == "Bunna Bank")
    all_ok &= check("listing: location", listing["location"] == "Addis Ababa, Ethiopia")

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

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
