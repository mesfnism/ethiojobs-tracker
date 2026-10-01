"""
Offline tests for combine_trackers.py's dedup logic, against synthetic
rows constructed to exercise: an exact cross-source duplicate, a
same-employer-same-title-same-location pair that's too far apart in time
(a real repost, not a duplicate), and two same-source rows that happen to
share employer/title/location (never merged, regardless of date).
"""

from combine_trackers import combine, normalize_employer, normalize_title

def row(job_id, title, employer, location, source, date_scraped, **extra):
    r = {
        "job_id": job_id, "job_title": title, "employer": employer,
        "category": extra.get("category"), "location": location,
        "work_type": None, "career_level": None,
        "employment_type": extra.get("employment_type", "Full Time"),
        "number_required": extra.get("number_required"),
        "education_required": extra.get("education_required"),
        "years_experience_required": extra.get("years_experience_required"),
        "skills_required": extra.get("skills_required"),
        "special_skill_training": None, "salary_hint": extra.get("salary_hint"),
        "application_deadline": None, "how_to_apply": None,
        "source_url": f"https://example.com/{source}/{job_id}",
        "source": source, "date_posted_relative": None,
        "date_scraped": date_scraped, "extraction_method": "listing_only",
    }
    return r


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    # Case 1: same vacancy, cross-posted to both sites 2 days apart -> merge
    rows = [
        row("e1", "Finance Manager", "SINEGA MANUFACTURING PLC", "Addis Ababa",
            "EthioJobs", "2026-09-24 10:00:00 UTC", education_required="Bachelor's degree"),
        row("h1", "Finance Manager", "Sinega Manufacturing", "Addis Ababa",
            "HaHuJobs", "2026-09-26 09:00:00 UTC", salary_hint="Birr 20000/Mon"),
    ]
    combined, dup_count = combine(rows)
    all_ok &= check("case1: merges into 1 row", len(combined) == 1)
    all_ok &= check("case1: dup_count == 1", dup_count == 1)
    if combined:
        m = combined[0]
        all_ok &= check("case1: source lists both", m["source"] == "EthioJobs, HaHuJobs")
        all_ok &= check("case1: keeps education from EthioJobs row",
                         m["education_required"] == "Bachelor's degree")
        all_ok &= check("case1: keeps salary from HaHuJobs row",
                         m["salary_hint"] == "Birr 20000/Mon")
        all_ok &= check("case1: other_source_url set", m["other_source_url"] is not None)

    # Case 2: same employer/title/location but 60 days apart -> real repost, not merged
    rows2 = [
        row("e2", "Accountant", "MEGA LAB PLC", "Dire Dawa", "EthioJobs", "2026-07-01 10:00:00 UTC"),
        row("h2", "Accountant", "MEGA LAB PLC", "Dire Dawa", "HaHuJobs", "2026-09-15 10:00:00 UTC"),
    ]
    combined2, dup_count2 = combine(rows2)
    all_ok &= check("case2: not merged (too far apart in time)", len(combined2) == 2)
    all_ok &= check("case2: dup_count == 0", dup_count2 == 0)

    # Case 3: same source, same employer/title/location, close in time -> never merged
    rows3 = [
        row("e3", "Salesperson", "INFINITY BUISNESS GROUP PLC", "Addis Ababa", "EthioJobs", "2026-09-24 10:00:00 UTC"),
        row("e4", "Salesperson", "INFINITY BUISNESS GROUP PLC", "Addis Ababa", "EthioJobs", "2026-09-24 11:00:00 UTC"),
    ]
    combined3, dup_count3 = combine(rows3)
    all_ok &= check("case3: same-source rows never merged", len(combined3) == 2)
    all_ok &= check("case3: dup_count == 0", dup_count3 == 0)

    # Normalization checks
    all_ok &= check("normalize_employer strips PLC/punctuation",
                     normalize_employer("SINEGA MANUFACTURING PLC") == normalize_employer("Sinega Manufacturing"))
    all_ok &= check("normalize_title strips Re-Advertised suffix",
                     normalize_title("Senior Officer (Re-Advertised)") == normalize_title("Senior Officer"))

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
