"""
Offline tests for source_health.py, built on small synthetic .xlsx
tracker files written to a temp directory (not the real production
trackers), covering:
  - a healthy, active source
  - a source that has gone quiet for several runs in a row (stalled)
  - a source whose tracker file doesn't exist yet (no_data_yet)
This is exactly the shape ReporterJobs/PalmJobs vs. a normal source
takes in real production data, which is what this scorecard exists to
surface honestly.
"""

import os
import tempfile

import openpyxl

import source_health as sh


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def make_tracker(path, run_log_rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(["job_id", "job_title"])
    for i in range(3):
        ws.append([f"id{i}", "Some Job"])
    log = wb.create_sheet("Run Log")
    log.append(["run_timestamp_utc", "new_rows", "running_total"])
    for row in run_log_rows:
        log.append(row)
    wb.save(path)


def main():
    all_ok = True
    tmpdir = tempfile.mkdtemp()

    active_path = os.path.join(tmpdir, "active_tracker.xlsx")
    make_tracker(active_path, [
        ["2026-09-28 06:00:00 UTC", 2, 10],
        ["2026-09-29 06:00:00 UTC", 0, 10],
        ["2026-09-30 06:00:00 UTC", 3, 13],
    ])

    stalled_path = os.path.join(tmpdir, "stalled_tracker.xlsx")
    make_tracker(stalled_path, [
        ["2026-09-26 06:00:00 UTC", 0, 5],
        ["2026-09-27 06:00:00 UTC", 0, 5],
        ["2026-09-28 06:00:00 UTC", 0, 5],
        ["2026-09-29 06:00:00 UTC", 0, 5],
    ])

    missing_path = os.path.join(tmpdir, "missing_tracker.xlsx")

    active_score = sh.score_source("ActiveSource", active_path)
    all_ok &= check("active source scores as active", active_score["status"] == "active")
    all_ok &= check("active source's last_run_new_postings is correct", active_score["last_run_new_postings"] == 3)
    all_ok &= check("active source's total_runs_logged is correct", active_score["total_runs_logged"] == 3)

    stalled_score = sh.score_source("ReporterJobs", stalled_path)
    all_ok &= check("a source with 4 consecutive zero-new-postings runs scores as stalled", stalled_score["status"] == "stalled")
    all_ok &= check("stalled source's consecutive_zero_new_runs is correct", stalled_score["consecutive_zero_new_runs"] == 4)
    all_ok &= check("stalled source's note flags it for investigation, doesn't claim it's broken outright",
                     "investigating" in stalled_score["note"])

    missing_score = sh.score_source("PalmJobs", missing_path)
    all_ok &= check("a source with no tracker file yet scores as no_data_yet, not a crash", missing_score["status"] == "no_data_yet")

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
