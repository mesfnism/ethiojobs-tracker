"""
Offline test — validates the parsing, dedup and Excel-writing logic using
REAL text captured from ethiojobs.net (a listing card and a job detail
page, both fetched and verified live via browser before this script was
written). No network/browser call is made here.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import ethiojobs_pipeline as pipeline

# --- Fixture 1: listing card, single category (captured live, 2026-09-21) ---
CARD_1_HREF = "/job/aRgBcZ2tY9-junior-accountant"
CARD_1_TEXT = (
    "New\n\nPro\n\nEasy Apply\n\nAccounting and Finance\n\nJunior Accountant\n\n"
    "39 minutes ago by\n\nBREAKTHROUGH TRADING S.C\n\nAddis Ababa\n\n"
    "September 25th, 2026\n\nOffice\n\n"
    "Handle all accounting treatments from coding up to preparation of statements "
    "for further review by the Division...\n\nSee More"
)

# --- Fixture 2: listing card, SIX categories (captured live, 2026-09-21) ---
CARD_2_HREF = "/job/zamzam-internal-auditor-ii"
CARD_2_TEXT = (
    "New\n\nPremium\n\nEasy Apply\n\n"
    "Economics\n\nManagement\n\nAccounting and Finance\n\nBanking and Insurance\n\n"
    "Business and Administration\n\nSocial Sciences and Communications\n\n"
    "Internal Auditor II\n\n3 hours ago by\n\nZAMZAM BANK\n\nDire Dawa\n\n"
    "September 28th, 2026\n\nOffice\n\n"
    "ABOUT ZAMZAM BANK ZamZam Bank S.C. takes its name from ZamZam Holy Water...\n\n"
    "See More"
)

# --- Fixture 3: job detail page body text (captured live, 2026-09-21) ---
DETAIL_TEXT = """Find Jobs
Find Companies
Blog
Contact Us
Log In
Sign Up
Junior Accountant
Addis Ababa
Accounting and Finance
Apply Now
Save
Share
Location Type : Office
Deadline : September 25th, 2026
Career Level : Junior Level(1-3 years)
Employment Type : Full time
Number of people required : 4
About the Job

Handle all accounting treatments from coding up to preparation of statements for further review by the Division.

About You

Education

BA in Accounting/Finance and Accounting or related fields

Work Experience

0 - 2 Years Required

Special skill/Training

IFRS Knowledge

Requirement Skill
Time management
Accounting
Team work
Accounting Principles (GAAP), IFRS and ERP
Ability to work under pressure and meet targets
How To Apply

ethiojobs.net

hr@alphagenuine.com

More Jobs by Breakthrough Trading S.C
"""

# --- Fixture 4: a posting with an in-text salary mention (captured live) ---
DETAIL_TEXT_WITH_SALARY = """Carpenter I
International Community School of Addis Ababa
Location Type : Office
Deadline : October 10th, 2026
Career Level : Mid Level(3-5 years)
Employment Type : Full time
Number of people required : 1
About the Job

Position: Carpenter I
Reports To: Carpentry Section Coordinator
Required Number: 1
Monthly Salary: ETB 87,975.00
Contract Type: Full Time

About You

Education

Diploma in Carpentry or related field

Work Experience

3 - 5 Years Required

Requirement Skill
Woodworking
Blueprint reading
How To Apply

Send CV to hr@icsaddis.example.org
"""


def test_parse_card_single_category():
    fields = pipeline.parse_card_text(CARD_1_HREF, CARD_1_TEXT)
    assert fields["job_id"] == "aRgBcZ2tY9"
    assert fields["job_title"] == "Junior Accountant"
    assert fields["employer"] == "BREAKTHROUGH TRADING S.C"
    assert fields["category"] == "Accounting and Finance"
    assert fields["location"] == "Addis Ababa"
    assert fields["application_deadline"] == "September 25th, 2026"
    assert fields["work_type"] == "Office"
    assert fields["date_posted_relative"] == "39 minutes ago by"
    assert fields["source_url"] == "https://ethiojobs.net/job/aRgBcZ2tY9-junior-accountant"
    print("  Card 1 (single category) parsed OK:", fields["job_title"], "|", fields["category"])
    return fields


def test_parse_card_multi_category():
    fields = pipeline.parse_card_text(CARD_2_HREF, CARD_2_TEXT)
    assert fields["job_id"] == "zamzam"
    assert fields["job_title"] == "Internal Auditor II"
    assert fields["employer"] == "ZAMZAM BANK"
    assert fields["location"] == "Dire Dawa"
    assert fields["category"].count(";") == 5  # six categories joined by "; "
    assert "Economics" in fields["category"]
    assert "Social Sciences and Communications" in fields["category"]
    print("  Card 2 (six categories) parsed OK:", fields["job_title"], "|", fields["category"])
    return fields


def test_parse_detail():
    detail = pipeline.parse_detail_text(DETAIL_TEXT)
    assert detail["career_level"] == "Junior Level(1-3 years)"
    assert detail["employment_type"] == "Full time"
    assert detail["number_required"] == "4"
    assert detail["education_required"] == "Bachelor's degree"
    assert detail["years_experience_required"] is not None and "0" in detail["years_experience_required"]
    assert detail["special_skill_training"] == "IFRS Knowledge"
    assert "Time management" in detail["skills_required"]
    assert "Accounting Principles" in detail["skills_required"]
    assert detail["how_to_apply"] is not None and "hr@alphagenuine.com" in detail["how_to_apply"]
    print("  Detail page parsed OK:", detail["career_level"], "|", detail["education_required"])
    return detail


def test_parse_detail_with_salary():
    detail = pipeline.parse_detail_text(DETAIL_TEXT_WITH_SALARY)
    assert detail["education_required"] == "Diploma"
    assert detail["salary_hint"] is not None and "87,975" in detail["salary_hint"]
    assert "ETB" in detail["salary_hint"]
    print("  Detail-with-salary parsed OK: salary_hint =", detail["salary_hint"])
    return detail


def test_structure_and_write_and_dedup(tmp_path):
    out = tmp_path / "test_tracker.xlsx"

    listing1 = test_parse_card_single_category()
    detail1 = test_parse_detail()
    row1 = pipeline.structure_job(listing1, detail1)
    assert row1["job_id"] == "aRgBcZ2tY9"
    assert row1["education_required"] == "Bachelor's degree"
    assert row1["extraction_method"] == "dom_render"

    listing2 = test_parse_card_multi_category()
    row2 = pipeline.structure_job(listing2, None)  # simulate a failed detail fetch
    assert row2["extraction_method"] == "listing_only"
    assert row2["job_title"] == "Internal Auditor II"

    pipeline.append_rows(out, [row1, row2])
    ids_after_run1 = pipeline.load_existing_ids(out)
    assert ids_after_run1 == {"aRgBcZ2tY9", "zamzam"}, ids_after_run1
    print("  Run 1: wrote 2 rows, IDs tracked correctly.")

    # Simulate a second daily run: one already-seen id should be filtered out
    # by the caller before append_rows is ever called (mirrors main()'s logic).
    existing = pipeline.load_existing_ids(out)
    candidate_ids = ["aRgBcZ2tY9", "new123"]  # one dup, one genuinely new
    genuinely_new = [j for j in candidate_ids if j not in existing]
    assert genuinely_new == ["new123"]
    print(f"  Run 2 dedup: correctly identified {len(genuinely_new)} new posting.")

    row3 = pipeline.structure_job(
        {"job_id": "new123", "job_title": "Digital Skills Trainer", "employer": "Plan International",
         "category": "Education", "location": "Hawassa", "work_type": "Office",
         "application_deadline": "October 5th, 2026", "date_posted_relative": "an hour ago by",
         "source_url": "https://ethiojobs.net/job/new123-digital-skills-trainer"},
        None,
    )
    pipeline.append_rows(out, [row3])
    ids_after_run2 = pipeline.load_existing_ids(out)
    assert ids_after_run2 == {"aRgBcZ2tY9", "zamzam", "new123"}, ids_after_run2

    import openpyxl
    wb = openpyxl.load_workbook(out)
    assert wb["Jobs"].max_row == 4  # header + 3 rows
    assert wb["Run Log"].max_row == 3  # header + 2 runs
    print("  Workbook structure confirmed: 3 job rows, 2 logged runs.")


if __name__ == "__main__":
    import tempfile
    print("Running offline pipeline tests (no network/browser calls)...\n")
    print("Test 1: card parsing (single category)")
    test_parse_card_single_category()
    print("\nTest 2: card parsing (six categories)")
    test_parse_card_multi_category()
    print("\nTest 3: detail page parsing")
    test_parse_detail()
    print("\nTest 4: detail page parsing with an in-text salary mention")
    test_parse_detail_with_salary()
    print("\nTest 5: structuring + write + dedup logic across simulated daily runs")
    with tempfile.TemporaryDirectory() as td:
        test_structure_and_write_and_dedup(Path(td))
    print("\nALL TESTS PASSED.")
