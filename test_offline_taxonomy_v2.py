"""
Offline tests for the newer taxonomy.py additions: the skill-noise filter,
title-case display helper, experience bucketing, education-specialization
extraction, and employer-type classification. Run with no network/browser
calls, against fixed example text (including the two garbage "skill"
examples the user actually saw on the live dashboard).
"""

import taxonomy as t


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    # Skill-noise filter — the two real garbage examples from the dashboard
    all_ok &= check(
        "rejects sentence-fragment skill",
        t.normalize_skill("a genuine passion for education and student development") is None,
    )
    all_ok &= check(
        "rejects comma-joined category list tagged as a skill",
        t.normalize_skill("Education, Social Work, Project Management") is None,
    )
    all_ok &= check("still accepts a real skill", t.normalize_skill("teamwork") == "Teamwork")
    all_ok &= check(
        "still accepts a real multi-word skill",
        t.normalize_skill("attention to detail") == "Attention to detail",
    )

    # Title-casing
    all_ok &= check("title-cases ALL CAPS", t.title_case_label("ACCOUNTING AND FINANCE") == "Accounting and Finance")
    all_ok &= check("title-cases all lowercase", t.title_case_label("addis ababa") == "Addis Ababa")
    all_ok &= check("keeps known acronym upper", "NGO" in t.title_case_label("ngo coordination"))
    all_ok &= check("keeps PLC upper in employer name", t.title_case_label("sinega manufacturing plc").endswith("PLC"))

    # Experience bucketing
    all_ok &= check("buckets '2 years' -> 1-3 years", t.experience_bucket("2 years") == "1-3 years")
    all_ok &= check("buckets '3-5 years' -> 3-5 years", t.experience_bucket("3-5 years") == "3-5 years")
    all_ok &= check("buckets 'Fresh Graduate' -> 0-1 years", t.experience_bucket("Fresh Graduate") == "0-1 years")
    all_ok &= check("buckets 'minimum 10 years' -> 8+ years", t.experience_bucket("minimum 10 years") == "8+ years")
    all_ok &= check("no number -> None", t.experience_bucket("Negotiable") is None)
    all_ok &= check("blank -> None", t.experience_bucket(None) is None)

    # Education specialization extraction
    fields = t.extract_specializations(
        "Must hold a PhD in Agricultural Economics or a related field. "
        "Experience in Project Management is a plus."
    )
    all_ok &= check("extracts specific field before generic", "Agricultural Economics" in fields)
    all_ok &= check("extracts a second field", "Project Management" in fields)
    all_ok &= check("does not double count substring field", fields.count("Agricultural Economics") == 1)
    all_ok &= check("no description -> empty list", t.extract_specializations(None) == [])
    all_ok &= check("no description -> empty list (empty string)", t.extract_specializations("") == [])

    # Employer type
    all_ok &= check("PLC -> Private", t.employer_type("Sinega Manufacturing PLC") == "Private")
    all_ok &= check("Ministry -> Public", t.employer_type("Ministry of Education") == "Public")
    all_ok &= check("University -> Public", t.employer_type("Addis Ababa University") == "Public")
    all_ok &= check(
        "Save the Children -> NGO",
        t.employer_type("Save the Children International") == "NGO",
    )
    all_ok &= check("unmatched name -> Unclassified", t.employer_type("Alpha Beta Gamma") == "Unclassified")
    all_ok &= check("blank -> None", t.employer_type(None) is None)

    # Salary bucketing
    all_ok &= check("'Birr 20000/Mon' -> 20,000-35,000 bucket", t.salary_bucket("Birr 20000/Mon") == "20,000–35,000 ETB")
    all_ok &= check("'ETB 87,975.00' -> 60,000+ bucket", t.salary_bucket("ETB 87,975.00") == "60,000+ ETB")
    all_ok &= check("'Negotiable' -> None (no currency/month marker)", t.salary_bucket("Negotiable") is None)
    all_ok &= check("'As per company scale' -> None", t.salary_bucket("As per company scale") is None)
    all_ok &= check("bare number with no marker -> None", t.salary_bucket("5") is None)
    all_ok &= check("range 'Birr 15,000 - 20,000' -> averaged bucket", t.salary_bucket("Birr 15,000 - 20,000") == "10,000–20,000 ETB")
    all_ok &= check("blank -> None", t.salary_bucket(None) is None)

    # Sector sub-category cleanup
    all_ok &= check(
        "normalizes category casing",
        t.normalize_category_label("ACCOUNTING AND FINANCE") == "Accounting and Finance",
    )
    all_ok &= check("rejects a bad/placeholder category label", t.normalize_category_label("Other") is None)
    all_ok &= check("blank -> None", t.normalize_category_label("") is None)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
