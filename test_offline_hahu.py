"""
Offline tests for hahujobs_pipeline.py's parse_card(), run against real text
captured directly from hahu.jobs's live DOM (no network/browser call here).
Run after any change to hahujobs_pipeline.py:

    python test_offline_hahu.py
"""

from hahujobs_pipeline import parse_card, extract_education

CARD_NO_SALARY = (
    "https://hahu.jobs/jobs/6aa8e04bdd4c307872960b53",
    "About 8 Hours Left\n\nSave\n\nAlpha Post Tension PLC\n\nLogistics Coordinator\n\n"
    "Transportation & Logistics\n\nTransportation Management\n\nAddis Ababa\n\n"
    "1 years\n\n1 Position\n\nFull Time\n\n"
    "TVET Level II or equivalent qualification in any field of study with relevant "
    "work experience Duties and Responsibilities - Coordinate and monitor the daily "
    "utilization of company vehicles. - Prepare and maintain accurate vehicle "
    "assignment, movement, and trip records.\n\nRead More\n\nApply Now\n\n9.9k",
)

CARD_WITH_SALARY = (
    "https://hahu.jobs/jobs/salary-test-id",
    "About 8 Hours Left\n\nSave\n\nSaglan Wajee General Hospital\n\nCall Center Agent\n\n"
    "Business\n\nBusiness Administration\n\nHoleta\n\n0 years\n\n2 Positions\n\n"
    "Full Time\n\nBirr 16483/Mon\n\n"
    "Bachelor's Degree in Nursing from a recognized government university with "
    "relevant work experience. Duties & Responsibilities: - Handle incoming calls, "
    "inquiries, and service requests professionally.\n\nRead More\n\nApply Now\n\n4.8k",
)

CARD_AMHARIC = (
    "https://hahu.jobs/jobs/amharic-test-id",
    "About 8 Hours Left\n\nSave\n\nIn-joy Burger\n\nChef\n\nHospitality\n\n"
    "Food Preparation\n\nAddis Ababa\n\n2 years - 4 years\n\n8 Positions\n\nPart Time\n\n"
    "በምግብ ዝግጅት ወይም በተመሳሳይ የትምህርት መስክ ሰርተፊኬት ያለው/ላት አግባብነት ካለው የስራ ልምድ ጋር "
    "ዋና ዋና ኃላፊነቶች - በምግብ ዝግጅት መመሪያ ወይም ማንዋል መሰረት ቅድመ ምግብ ማዘጋጀት.\n\n"
    "Read More\n\nApply Now\n\n2.1k",
)


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    fields, ok = parse_card(*CARD_NO_SALARY)
    all_ok &= check("no-salary: ok flag true", ok)
    all_ok &= check("no-salary: job_id", fields["job_id"] == "6aa8e04bdd4c307872960b53")
    all_ok &= check("no-salary: employer", fields["employer"] == "Alpha Post Tension PLC")
    all_ok &= check("no-salary: job_title", fields["job_title"] == "Logistics Coordinator")
    all_ok &= check("no-salary: category", fields["category"] ==
                     "Transportation & Logistics; Transportation Management")
    all_ok &= check("no-salary: location", fields["location"] == "Addis Ababa")
    all_ok &= check("no-salary: experience", fields["years_experience_required"] == "1 years")
    all_ok &= check("no-salary: positions", fields["number_required"] == 1)
    all_ok &= check("no-salary: employment_type", fields["employment_type"] == "Full Time")
    all_ok &= check("no-salary: salary_hint is None", fields["salary_hint"] is None)
    all_ok &= check("no-salary: education (TVET->Certificate)",
                     fields["education_required"] == "Certificate")
    all_ok &= check("no-salary: deadline_relative", fields["application_deadline"] == "About 8 Hours Left")

    fields, ok = parse_card(*CARD_WITH_SALARY)
    all_ok &= check("salary: ok flag true", ok)
    all_ok &= check("salary: salary_hint", fields["salary_hint"] == "Birr 16483/Mon")
    all_ok &= check("salary: number_required", fields["number_required"] == 2)
    all_ok &= check("salary: education (Bachelor's)",
                     fields["education_required"] == "Bachelor's degree")

    fields, ok = parse_card(*CARD_AMHARIC)
    all_ok &= check("amharic: ok flag true (header fields still parse)", ok)
    all_ok &= check("amharic: employer", fields["employer"] == "In-joy Burger")
    all_ok &= check("amharic: category", fields["category"] == "Hospitality; Food Preparation")
    all_ok &= check("amharic: employment_type", fields["employment_type"] == "Part Time")
    all_ok &= check("amharic: education unclassified (no English education pattern)",
                     fields["education_required"] is None)

    all_ok &= check("extract_education: none found -> None", extract_education("no keywords here") is None)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
