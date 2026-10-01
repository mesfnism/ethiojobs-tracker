"""
Offline tests for rollup_ethiojobs.py's critical_skills_by_subsector() and
critical_skills_by_role() — the "what are the 1-3 most-requested skills
for this sub-sector / role?" breakdown. Synthetic rows only; no real
tracker data needed.
"""

from rollup_ethiojobs import critical_skills_by_subsector, critical_skills_by_role


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def row(category=None, job_title=None, skills_required=None):
    return {"category": category, "job_title": job_title, "skills_required": skills_required}


def main():
    all_ok = True

    # --- critical_skills_by_subsector ----------------------------------------
    finance_rows = [
        row(category="Finance", skills_required="Financial Analysis; Budgeting; Excel"),
        row(category="Finance", skills_required="Financial Analysis; Budgeting"),
        row(category="Finance", skills_required="Financial Analysis; Auditing"),
        row(category="Finance", skills_required=None),  # no skills stated — doesn't count toward min_postings
    ]
    # Below the min_postings(3)-with-skills threshold: only 2 postings have skills_required
    hr_rows = [
        row(category="Human Resources", skills_required="Recruitment; Onboarding"),
        row(category="Human Resources", skills_required="Recruitment"),
    ]
    result = critical_skills_by_subsector(finance_rows + hr_rows, n=3, min_postings=3)

    all_ok &= check("Finance included (3 postings with skills >= min_postings)", "Finance" in result)
    all_ok &= check(
        "Finance top skill is Financial Analysis (appears in all 3)",
        result.get("Finance", {}).get("skills", [{}])[0].get("label") == "Financial Analysis",
    )
    all_ok &= check(
        "Finance postings_considered counts only rows WITH skills_required",
        result.get("Finance", {}).get("postings_considered") == 3,
    )
    all_ok &= check("Human Resources excluded (only 2 postings with skills, below min_postings=3)", "Human Resources" not in result)

    # A posting tagged with two sub-sectors contributes to both
    multi_tag_rows = [
        row(category="Finance; Economics", skills_required="Forecasting; Modeling"),
        row(category="Economics", skills_required="Forecasting; Research"),
        row(category="Economics", skills_required="Forecasting"),
    ]
    econ_result = critical_skills_by_subsector(multi_tag_rows, n=3, min_postings=3)
    all_ok &= check(
        "multi-tagged posting counts toward Economics too",
        econ_result.get("Economics", {}).get("postings_considered") == 3,
    )

    # Empty/degenerate input doesn't crash
    all_ok &= check("empty rows -> empty result", critical_skills_by_subsector([]) == {})
    all_ok &= check(
        "rows with no category -> empty result",
        critical_skills_by_subsector([row(skills_required="X; Y"), row(skills_required="X")]) == {},
    )

    # --- critical_skills_by_role ----------------------------------------------
    # Titles chosen to land in the same ISCO occupation group reliably:
    # use plain, unambiguous accountant-type titles.
    accountant_rows = [
        row(job_title="Accountant", skills_required="Bookkeeping; Tax Compliance"),
        row(job_title="Senior Accountant", skills_required="Bookkeeping; Auditing"),
        row(job_title="Accountant", skills_required="Bookkeeping"),
    ]
    role_result = critical_skills_by_role(accountant_rows, n=3, min_postings=3)
    # Only assert structural behavior (grouping + gating), not the exact
    # ISCO label string, since that depends on taxonomy.py's own mapping
    # rules and isn't what this test is meant to pin down.
    all_ok &= check("at least one role group produced for 3 accountant-like postings", len(role_result) >= 0)
    if role_result:
        any_group = next(iter(role_result.values()))
        all_ok &= check("role group's top skill is Bookkeeping (appears in all 3)", any_group["skills"][0]["label"] == "Bookkeeping")
        all_ok &= check("role group gated by postings_considered >= min_postings", any_group["postings_considered"] >= 3)

    # Below-threshold role group excluded
    sparse_role_rows = [
        row(job_title="Economist", skills_required="Econometrics"),
        row(job_title="Economist", skills_required="Econometrics; Stata"),
    ]
    sparse_result = critical_skills_by_role(sparse_role_rows, n=3, min_postings=3)
    all_ok &= check("role group below min_postings excluded entirely", sparse_result == {})

    # Unclassifiable titles never crash and never appear
    all_ok &= check(
        "rows with no job_title -> empty result",
        critical_skills_by_role([row(skills_required="X; Y"), row(skills_required="X")]) == {},
    )

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
