"""
Source Health Scorecard — reads each source tracker's own "Run Log" sheet
(written by that source's pipeline on every run) and produces a small,
honest reliability summary per source: how many runs have been logged,
when the last one ran, how many new postings it added, and whether it
has gone quiet (several consecutive runs adding zero new postings).

This needs no new scraping logic. Every pipeline already writes a Run
Log row on each run; this script is the first thing to actually read
that history back and turn it into a signal, rather than leaving it
sitting unused inside each tracker file.

A source that has gone quiet is NOT necessarily broken. A genuinely
exhausted job board can post nothing new for days. This scorecard flags
a stalled streak as something to look at, not as a confirmed failure,
and it reports a source that has never produced a file at all
("no_data_yet") distinctly from one that ran but found nothing
("stalled") or one that is actively adding postings ("active") — so a
source such as ReporterJobs or PalmJobs that is currently near-silent in
production shows up honestly as stalled or quiet, rather than being
hidden or implied healthy.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

OUTPUT_JSON = "docs/data/source_health.json"
STALLED_STREAK_THRESHOLD = 3  # consecutive zero-new-row runs before flagging "stalled"
RECENT_RUNS_WINDOW = 5

# (display name, tracker filename) — kept as its own list here rather than
# importing combine_trackers.INPUT_FILES/SOURCE_LOG_ORDER, so this script
# has no import-time dependency on that module and can be run on its own.
SOURCES = [
    ("EthioJobs", "ethiojobs_tracker.xlsx"),
    ("HaHuJobs", "hahujobs_tracker.xlsx"),
    ("ReporterJobs", "reporterjobs_tracker.xlsx"),
    ("PalmJobs", "palmjobs_tracker.xlsx"),
    ("DevNetJobs", "devnetjobs_tracker.xlsx"),
    ("HarmeeJobs", "harmeejobs_tracker.xlsx"),
    ("ElelanaJobs", "elelanajobs_tracker.xlsx"),
    ("KebenaJobs", "kebenajobs_tracker.xlsx"),
    ("Afriworket", "afriworket_tracker.xlsx"),
    ("GeezJobs", "geezjobs_tracker.xlsx"),
]

# Every pipeline's Run Log sheet has "run_timestamp_utc" first and a
# "new_rows"-style column somewhere after it, but the exact column name
# for "new postings this run" differs slightly across pipelines (some
# call it "new_rows", HaHuJobs may use a differently named column) — so
# this looks for any column whose name contains "new" rather than
# assuming one fixed name, and falls back to None (unknown) rather than
# guessing 0 when no such column is found.
def _find_new_rows_column(header):
    for i, name in enumerate(header):
        if name and "new" in str(name).lower() and "row" in str(name).lower():
            return i
    for i, name in enumerate(header):
        if name and str(name).lower() == "new_postings":
            return i
    return None


def read_run_log(path):
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except FileNotFoundError:
        return None, None
    if "Run Log" not in wb.sheetnames:
        wb.close()
        return [], None
    log_ws = wb["Run Log"]
    header = [c.value for c in next(log_ws.iter_rows(min_row=1, max_row=1))]
    runs = [dict(zip(header, r)) for r in log_ws.iter_rows(min_row=2, values_only=True)]
    new_rows_col = _find_new_rows_column(header)

    total_postings = None
    if "Jobs" in wb.sheetnames:
        total_postings = wb["Jobs"].max_row - 1

    wb.close()
    return runs, (new_rows_col, header, total_postings)


def parse_run_timestamp(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def score_source(name, path):
    runs, meta = read_run_log(path)

    if runs is None:
        return {
            "source": name,
            "status": "no_data_yet",
            "note": f"{path} has not been produced by a run yet.",
        }

    if not runs:
        return {
            "source": name,
            "status": "no_run_log",
            "note": "Tracker file exists but has no Run Log sheet to read.",
        }

    new_rows_col, header, total_postings = meta
    new_row_counts = []
    for run in runs:
        if new_rows_col is not None:
            val = run.get(header[new_rows_col])
            new_row_counts.append(val if isinstance(val, (int, float)) else None)
        else:
            new_row_counts.append(None)

    last_run = runs[-1]
    last_timestamp = parse_run_timestamp(last_run.get("run_timestamp_utc"))

    # Consecutive-zero streak, counted backward from the most recent run,
    # only over runs where new-row counts are actually known.
    streak = 0
    for val in reversed(new_row_counts):
        if val is None:
            break
        if val == 0:
            streak += 1
        else:
            break

    recent = [v for v in new_row_counts[-RECENT_RUNS_WINDOW:] if v is not None]
    recent_total = sum(recent) if recent else None

    if streak >= STALLED_STREAK_THRESHOLD:
        status = "stalled"
    elif new_row_counts and new_row_counts[-1] not in (None, 0):
        status = "active"
    elif new_row_counts and new_row_counts[-1] == 0:
        status = "quiet"
    else:
        status = "unknown"

    return {
        "source": name,
        "status": status,
        "total_runs_logged": len(runs),
        "last_run_timestamp_utc": last_run.get("run_timestamp_utc"),
        "last_run_new_postings": new_row_counts[-1] if new_row_counts else None,
        "consecutive_zero_new_runs": streak,
        "new_postings_last_5_runs": recent_total,
        "current_total_postings": total_postings,
        "note": {
            "no_data_yet": "",
            "stalled": f"No new postings logged across the last {streak} consecutive runs. "
                       "This may reflect a genuinely exhausted source, or a pipeline problem "
                       "worth investigating directly against the live site.",
            "quiet": "The most recent run added no new postings, but this has not yet "
                     "repeated enough times to call it stalled.",
            "active": "",
            "unknown": "New-postings-per-run could not be read from this source's Run Log "
                       "column layout.",
        }[status],
    }


def main():
    scores = [score_source(name, path) for name, path in SOURCES]

    stalled = [s["source"] for s in scores if s["status"] == "stalled"]
    no_data = [s["source"] for s in scores if s["status"] == "no_data_yet"]

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "stalled_streak_threshold": STALLED_STREAK_THRESHOLD,
        "sources": scores,
        "summary": {
            "stalled_sources": stalled,
            "sources_with_no_data_yet": no_data,
        },
    }

    out_path = Path(OUTPUT_JSON)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))

    print(f"Wrote {out_path} for {len(scores)} sources.")
    if stalled:
        print(f"  [flag] stalled (>= {STALLED_STREAK_THRESHOLD} consecutive zero-new-posting runs): "
              f"{', '.join(stalled)}", file=sys.stderr)
    if no_data:
        print(f"  [info] no tracker file produced yet: {', '.join(no_data)}", file=sys.stderr)


if __name__ == "__main__":
    main()
