"""
Offline tests for generate_intelligence_report.py — history tracking,
rank-change detection, coverage-gap flagging, and HTML rendering. All
synthetic data; no network, no real rollups.json needed.
"""

import json
import tempfile
from pathlib import Path

from generate_intelligence_report import (
    condense_window,
    update_history,
    find_baseline,
    rank_changes,
    coverage_gaps,
    build_insights,
    render_report_html,
)


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def make_window(posting_count=20, skills=None, categories=None):
    skills = skills or [{"label": "Excel", "count": 10}, {"label": "Budgeting", "count": 8}]
    categories = categories or [{"label": "Finance", "count": 12}]
    return {
        "posting_count": posting_count,
        "top_skills": skills,
        "top_categories": categories,
        "top_jobs": [{"label": "Clerks", "count": 5}],
        "top_locations": [{"label": "Addis Ababa", "count": 15}],
        "top_employers": [{"label": "Acme Bank", "count": 4}],
        "education": {"known_total": 5, "total": 20},
        "experience": {"known_total": 18, "total": 20},
        "salary": {"known_total": 2, "total": 20},
        "employer_type": {"known_total": 20, "total": 20},
    }


def main():
    all_ok = True

    # --- condense_window --------------------------------------------------
    w = make_window()
    c = condense_window(w)
    all_ok &= check("condense_window keeps posting_count", c["posting_count"] == 20)
    all_ok &= check("condense_window keeps top skills", c["top_skills"][0]["label"] == "Excel")
    all_ok &= check("condense_window computes salary coverage", c["coverage"]["salary"] == {"known": 2, "total": 20})

    # --- update_history / find_baseline ------------------------------------
    with tempfile.TemporaryDirectory() as td:
        hist_path = Path(td) / "history.json"
        rollups_day1 = {
            "generated_at": "2026-09-01 06:00:00 UTC",
            "windows": {"weekly": make_window(posting_count=20), "monthly": make_window(posting_count=80)},
        }
        history = update_history(str(hist_path), rollups_day1, "2026-09-01")
        all_ok &= check("update_history appends one entry", len(history) == 1)

        rollups_day8 = {
            "generated_at": "2026-09-08 06:00:00 UTC",
            "windows": {
                "weekly": make_window(
                    posting_count=25,
                    skills=[{"label": "Python", "count": 12}, {"label": "Excel", "count": 9}],
                    categories=[{"label": "ICT", "count": 10}, {"label": "Finance", "count": 9}],
                ),
                "monthly": make_window(posting_count=90),
            },
        }
        history = update_history(str(hist_path), rollups_day8, "2026-09-08")
        all_ok &= check("update_history appends a second entry", len(history) == 2)

        # re-running the same day replaces rather than duplicating
        history = update_history(str(hist_path), rollups_day8, "2026-09-08")
        all_ok &= check("re-running same day does not duplicate", len(history) == 2)

        baseline = find_baseline(history, "2026-09-08", target_days_ago=7, tolerance_days=3)
        all_ok &= check("find_baseline finds the day-1 entry ~7 days back", baseline is not None and baseline["date"] == "2026-09-01")

        no_baseline = find_baseline(history, "2026-09-08", target_days_ago=30, tolerance_days=3)
        all_ok &= check("find_baseline returns None when nothing is close enough", no_baseline is None)

        # --- rank_changes -----------------------------------------------------
        current_skills = [{"label": "Python", "count": 12}, {"label": "Excel", "count": 9}]
        baseline_skills = [{"label": "Excel", "count": 10}, {"label": "Budgeting", "count": 8}]
        changes = rank_changes(current_skills, baseline_skills)
        all_ok &= check("new entrant detected (Python)", any(i["label"] == "Python" for i in changes["new_entrants"]))
        all_ok &= check("dropped detected (Budgeting)", any(i["label"] == "Budgeting" for i in changes["dropped"]))

        # --- coverage_gaps ------------------------------------------------------
        gaps = coverage_gaps(condense_window(make_window())["coverage"])
        gap_fields = {g["field"] for g in gaps}
        all_ok &= check("salary flagged as a coverage gap (2/20 = 10%)", "salary" in gap_fields)
        all_ok &= check("experience NOT flagged (18/20 = 90%)", "experience" not in gap_fields)

        # --- build_insights + render_report_html end-to-end, with baseline -----
        insights = build_insights(rollups_day8["windows"]["weekly"], baseline, "weekly")
        all_ok &= check("build_insights picks up baseline", insights["has_baseline"] is True)
        all_ok &= check("build_insights computes volume_change_pct", insights["volume_change_pct"] == 25.0)

        html = render_report_html(
            report_title="Vacancy and Skill Intelligence Report — Weekly",
            period_label="Covers the 7 days up to 2026-09-08",
            window_label="week",
            generated_at="2026-09-08 06:00:00 UTC",
            insights=insights,
            this_url_name="weekly-intelligence-report.html",
            other_report_link="monthly-intelligence-report.html",
            dashboard_link="../index.html",
        )
        all_ok &= check("rendered HTML contains the title", "Vacancy and Skill Intelligence Report" in html)
        all_ok &= check("rendered HTML mentions the new entrant", "Python" in html)
        all_ok &= check("rendered HTML flags the salary coverage gap", "GAP" in html)
        all_ok &= check("rendered HTML is well-formed enough to open a body tag", "<body>" in html and "</html>" in html)

        # --- build_insights + render, with NO baseline (first-ever run) --------
        no_base_insights = build_insights(make_window(), None, "weekly")
        all_ok &= check("no baseline -> has_baseline False", no_base_insights["has_baseline"] is False)
        html_first = render_report_html(
            report_title="Vacancy and Skill Intelligence Report — Weekly",
            period_label="Covers the 7 days up to 2026-09-01",
            window_label="week",
            generated_at="2026-09-01 06:00:00 UTC",
            insights=no_base_insights,
            this_url_name="weekly-intelligence-report.html",
            other_report_link="monthly-intelligence-report.html",
            dashboard_link="../index.html",
        )
        all_ok &= check("first-run HTML says it's a first snapshot, doesn't crash", "first tracked snapshot" in html_first)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
