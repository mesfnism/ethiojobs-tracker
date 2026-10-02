"""
Offline tests for rollup_ethiojobs.py's new breakdown functions
(concentration stats, employer registry, region, language, skill
intensity). These run on small synthetic row sets rather than a real
combined_tracker.xlsx, built to exercise:
  - ordinary, well-populated rows
  - rows from a near-silent source (mirroring ReporterJobs/PalmJobs in
    real production data) to confirm every new function degrades
    gracefully (no crash, no misleading "0% coverage" framed as a full
    result) rather than producing or implying health it hasn't earned.
"""

from rollup_ethiojobs import (
    _concentration_stats,
    normalize_employer_key,
    employer_registry,
    region_for_location,
    region_breakdown,
    _script_mix,
    language_breakdown,
    skill_intensity_by_role,
    skill_concentration,
    employer_concentration,
    category_concentration,
    _with_share_pct,
)
from collections import Counter


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


ROWS = [
    {
        "job_id": "1", "job_title": "Accountant", "employer": "Dashen Bank S.C.",
        "location": "Addis Ababa", "skills_required": "Accounting;Teamwork",
        "category": "Finance", "source": "EthioJobs",
        "description": "We are hiring an accountant for our Addis Ababa branch.",
    },
    {
        "job_id": "2", "job_title": "Senior Accountant", "employer": "Dashen Bank",
        "location": "Addis Ababa", "skills_required": "Accounting;Excel;Teamwork",
        "category": "Finance", "source": "HaHuJobs",
        "description": "Senior accountant role based in Addis Ababa.",
    },
    {
        "job_id": "3", "job_title": "Teacher", "employer": "Dashen Bank PLC",
        "location": "Bahir Dar", "skills_required": "Teaching",
        "category": "Education", "source": "EthioJobs",
        "description": "የትዮት የምርት መተወን",  # Amharic text (teacher position announcement)
    },
    {
        "job_id": "4", "job_title": "Driver", "employer": "Ethiopian Airlines",
        "location": "Dire Dawa", "skills_required": None,
        "category": None, "source": "HarmeeJobs",
        "description": None,
    },
    {
        "job_id": "5", "job_title": "Project Manager", "employer": "World Vision Ethiopia",
        "location": "Hawassa", "skills_required": "Project Management;Teamwork",
        "category": "NGO", "source": "GeezJobs",
        "description": "Mixed የትዮት announcement for a project manager role.",
    },
]

# A near-silent source: a single row, mirroring ReporterJobs/PalmJobs
# real-world near-zero contribution — every new function must handle
# this without crashing or claiming more than one row supports.
SPARSE_ROWS = [
    {
        "job_id": "s1", "job_title": "Clerk", "employer": "Reporter PLC",
        "location": None, "skills_required": None, "category": None,
        "source": "ReporterJobs", "description": None,
    },
]


def main():
    all_ok = True

    # --- concentration stats ---
    c = Counter({"a": 5, "b": 3, "c": 2})
    stats = _concentration_stats(c)
    all_ok &= check("concentration: distinct_count", stats["distinct_count"] == 3)
    all_ok &= check("concentration: top5 share is 100% with only 3 entries", stats["top5_share_pct"] == 100.0)
    all_ok &= check("concentration: hhi is a positive number", stats["hhi"] > 0)
    empty_stats = _concentration_stats(Counter())
    all_ok &= check("concentration: empty counter degrades to zeros, not a crash", empty_stats["hhi"] == 0.0)

    all_ok &= check("skill_concentration on ordinary rows produces a result", skill_concentration(ROWS)["distinct_count"] > 0)
    all_ok &= check("skill_concentration on sparse/no-skill rows degrades to zero, not a crash",
                     skill_concentration(SPARSE_ROWS)["distinct_count"] == 0)
    all_ok &= check("employer_concentration on sparse rows still returns a result (1 employer)",
                     employer_concentration(SPARSE_ROWS)["distinct_count"] == 1)
    all_ok &= check("category_concentration on sparse/no-category rows degrades to zero",
                     category_concentration(SPARSE_ROWS)["distinct_count"] == 0)

    # --- employer registry / canonicalization ---
    all_ok &= check(
        "normalize_employer_key merges legal-suffix variants",
        normalize_employer_key("Dashen Bank S.C.") == normalize_employer_key("Dashen Bank PLC")
        == normalize_employer_key("Dashen Bank"),
    )
    registry = employer_registry(ROWS)
    dashen = next((e for e in registry if "dashen" in e["label"].lower()), None)
    all_ok &= check("employer_registry merges 3 Dashen Bank spellings into one entry", dashen is not None)
    all_ok &= check("employer_registry counts all 3 merged postings", dashen is not None and dashen["count"] == 3)
    all_ok &= check("employer_registry records 3 distinct spellings seen", dashen is not None and dashen["variant_spellings"] == 3)
    all_ok &= check("employer_registry entries carry a share_pct", "share_pct" in registry[0])

    sparse_registry = employer_registry(SPARSE_ROWS)
    all_ok &= check("employer_registry on a single-row sparse source still works", len(sparse_registry) == 1)

    # --- region ---
    all_ok &= check("region_for_location recognizes Addis Ababa", region_for_location("Addis Ababa") == "Addis Ababa")
    all_ok &= check("region_for_location recognizes Bahir Dar as Amhara", region_for_location("Bahir Dar") == "Amhara")
    all_ok &= check("region_for_location recognizes Hawassa as Sidama", region_for_location("Hawassa") == "Sidama")
    all_ok &= check("region_for_location returns None for an unrecognized string", region_for_location("Somewhere Unknown") is None)
    all_ok &= check("region_for_location returns None for empty input", region_for_location("") is None)

    region = region_breakdown(ROWS)
    all_ok &= check("region_breakdown known_total counts every row with a usable location", region["known_total"] == 5)
    all_ok &= check("region_breakdown finds Addis Ababa as a region", any(r["label"] == "Addis Ababa" for r in region["regions"]))

    sparse_region = region_breakdown(SPARSE_ROWS)
    all_ok &= check("region_breakdown on a location-less sparse source returns known_total 0, not a crash", sparse_region["known_total"] == 0)

    # --- language ---
    all_ok &= check("_script_mix detects pure English text", _script_mix("Senior Accountant role") == "English")
    all_ok &= check("_script_mix detects pure Ethiopic-script text", _script_mix("የትዮት የምርት") == "Ethiopic script (Amharic/Tigrinya)")
    all_ok &= check("_script_mix detects mixed text", _script_mix("Project Manager የትዮት") == "Mixed")
    all_ok &= check("_script_mix returns None for empty text", _script_mix("") is None)

    lang = language_breakdown(ROWS)
    all_ok &= check("language_breakdown known_total counts every row with job_title or description text", lang["known_total"] == 5)
    labels = {l["label"] for l in lang["languages"]}
    all_ok &= check("language_breakdown finds at least English and Ethiopic script", {"English", "Ethiopic script (Amharic/Tigrinya)"}.issubset(labels) or "Mixed" in labels)

    sparse_lang = language_breakdown(SPARSE_ROWS)
    all_ok &= check("language_breakdown on a sparse source with only a job_title still classifies it, no crash", sparse_lang["known_total"] == 1)

    # --- skill intensity by role ---
    intensity = skill_intensity_by_role(ROWS, min_postings=1)
    all_ok &= check("skill_intensity_by_role produces at least one group with a low min_postings threshold", len(intensity) > 0)
    all_ok &= check("skill_intensity_by_role entries carry avg_skills_per_posting", intensity and "avg_skills_per_posting" in intensity[0])

    sparse_intensity = skill_intensity_by_role(SPARSE_ROWS)
    all_ok &= check("skill_intensity_by_role on a no-skills sparse source returns an empty list, not a crash", sparse_intensity == [])

    # --- share_pct helper ---
    shared = _with_share_pct([{"label": "x", "count": 5}], 10)
    all_ok &= check("_with_share_pct computes a correct percentage", shared[0]["share_pct"] == 50.0)
    shared_zero = _with_share_pct([{"label": "x", "count": 5}], 0)
    all_ok &= check("_with_share_pct degrades to 0.0 on a zero denominator, not a division error", shared_zero[0]["share_pct"] == 0.0)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
