"""
Vacancy and Skill Intelligence Report — turns the same rollups.json the
dashboard reads into two narrative HTML reports: a weekly one (the rolling
7-day window) and a monthly one (the rolling 30-day window). Where the
dashboard answers "what does the data look like right now", this answers
"what changed, what's persistent, and what's worth flagging" — by diffing
today's snapshot against one taken roughly a week (or a month) earlier.

Both reports are self-contained HTML files, written to docs/reports/, so
they publish on GitHub Pages automatically alongside the dashboard and can
be linked or shared directly whenever you want, with no extra build step.

How the "what changed" comparison works
----------------------------------------
rollups.json itself only ever holds the CURRENT state. To know what's
trending, we need to remember what things looked like before — so this
script keeps a small running history file, docs/data/report_history.json:
one compact entry per day this script has run, holding just enough of that
day's weekly/monthly numbers to diff against later (not the full
rollups.json — that would grow the repo for no reason). Every run:
  1. appends today's condensed snapshot,
  2. trims the history to the most recent ~400 entries (over a year, more
     than enough for week-over-week and month-over-month comparisons),
  3. looks back for the entry closest to 7 days ago (for the weekly
     report) and 30 days ago (for the monthly report), within a few days'
     tolerance, to use as the comparison baseline.
The very first time this runs, there's no baseline yet — the report says
so plainly ("first tracked snapshot") rather than inventing a trend from
nothing.

Run this AFTER rollup_ethiojobs.py in the same workflow, since it reads
rollup_ethiojobs.py's own output (docs/data/rollups.json).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROLLUPS_PATH = "docs/data/rollups.json"
HISTORY_PATH = "docs/data/report_history.json"
WEEKLY_REPORT_PATH = "docs/reports/weekly-intelligence-report.html"
MONTHLY_REPORT_PATH = "docs/reports/monthly-intelligence-report.html"

HISTORY_MAX_ENTRIES = 400
TOP_N_FOR_TRENDS = 10
CLIMB_THRESHOLD = 2          # rank positions moved up to count as a "climber"
COVERAGE_CONCERN_PCT = 50    # a field known for under this % of postings is flagged as a data gap

TRACKED_LISTS = ["top_skills", "top_categories", "top_jobs", "top_locations", "top_employers"]
LIST_DISPLAY_NAMES = {
    "top_skills": "skills",
    "top_categories": "sectors",
    "top_jobs": "job roles",
    "top_locations": "locations",
    "top_employers": "employers",
}
COVERAGE_FIELDS = {
    "education": "stating a clear education level",
    "experience": "stating years of experience required",
    "salary": "stating a clear monthly pay figure",
}


# ---------------------------------------------------------------------------
# History tracking
# ---------------------------------------------------------------------------

def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def condense_window(window_data):
    """Keeps only what a future comparison needs from one window's full
    summarize() payload — small enough that a year of daily entries stays
    a tiny file."""
    coverage = {}
    for field in COVERAGE_FIELDS:
        block = window_data.get(field) or {}
        total = block.get("total", 0)
        known = block.get("known_total", 0)
        coverage[field] = {"known": known, "total": total}
    employer_type = window_data.get("employer_type") or {}
    coverage["employer_type"] = {
        "known": employer_type.get("known_total", 0),
        "total": employer_type.get("total", 0),
    }

    condensed = {"posting_count": window_data.get("posting_count", 0), "coverage": coverage}
    for key in TRACKED_LISTS:
        items = window_data.get(key) or []
        condensed[key] = [{"label": i["label"], "count": i["count"]} for i in items[:TOP_N_FOR_TRENDS]]
    return condensed


def update_history(history_path, rollups, as_of_date):
    history = load_json(history_path, [])
    history = [h for h in history if h.get("date") != as_of_date]  # re-running same day replaces, doesn't duplicate
    history.append({
        "date": as_of_date,
        "weekly": condense_window(rollups["windows"]["weekly"]),
        "monthly": condense_window(rollups["windows"]["monthly"]),
    })
    history.sort(key=lambda h: h["date"])
    if len(history) > HISTORY_MAX_ENTRIES:
        history = history[-HISTORY_MAX_ENTRIES:]
    save_json(history_path, history)
    return history


def find_baseline(history, as_of_date, target_days_ago, tolerance_days=3):
    """The history entry whose date is closest to (as_of_date minus
    target_days_ago days), as long as it's within tolerance_days of that
    target — close enough to call it "a week ago" or "a month ago"
    without the comparison drifting into a different kind of window
    entirely. Never matches today's own just-appended entry."""
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    target = as_of - timedelta(days=target_days_ago)
    best, best_diff = None, None
    for entry in history:
        if entry["date"] == as_of_date:
            continue
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d")
        diff = abs((entry_date - target).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best, best_diff = entry, diff
    return best


# ---------------------------------------------------------------------------
# Trend analysis
# ---------------------------------------------------------------------------

def pct_change(current, baseline):
    if not baseline:
        return None
    return round(((current - baseline) / baseline) * 100, 1)


def rank_changes(current_items, baseline_items):
    """Diffs two ranked top-N lists (current vs baseline) by label, and
    sorts each into: persistent (near the same rank in both — the
    "nothing's changed here" signal), climbers (moved up by at least
    CLIMB_THRESHOLD ranks), new_entrants (in current, wasn't in baseline
    at all), dropped (was in baseline's top-N, isn't in current's)."""
    cur_rank = {item["label"]: i for i, item in enumerate(current_items)}
    cur_count = {item["label"]: item["count"] for item in current_items}
    base_rank = {item["label"]: i for i, item in enumerate(baseline_items)}

    persistent, climbers, new_entrants = [], [], []
    for label, rank in cur_rank.items():
        if label in base_rank:
            moved_up = base_rank[label] - rank
            if moved_up >= CLIMB_THRESHOLD:
                climbers.append({"label": label, "count": cur_count[label], "ranks_up": moved_up})
            elif abs(moved_up) <= 1 and rank < 3 and base_rank[label] < 3:
                persistent.append({"label": label, "count": cur_count[label]})
        else:
            new_entrants.append({"label": label, "count": cur_count[label]})

    dropped = [{"label": label} for label in base_rank if label not in cur_rank]
    climbers.sort(key=lambda d: -d["ranks_up"])
    return {
        "persistent": persistent,
        "climbers": climbers,
        "new_entrants": new_entrants,
        "dropped": dropped,
    }


def coverage_gaps(coverage):
    """Which stated-field coverage rates are below COVERAGE_CONCERN_PCT —
    a straightforward, honestly-reported data limitation, not a modeled
    finding."""
    gaps = []
    for field, label in COVERAGE_FIELDS.items():
        block = coverage.get(field, {})
        total = block.get("total", 0)
        known = block.get("known", 0)
        if total == 0:
            continue
        pct = round((known / total) * 100, 1)
        if pct < COVERAGE_CONCERN_PCT:
            gaps.append({"field": field, "label": label, "pct": pct, "known": known, "total": total})
    return gaps


def build_insights(window_data, baseline_entry, window_key):
    """window_key is "weekly" or "monthly" — selects which half of a
    history entry to compare against."""
    current = condense_window(window_data)
    result = {
        "current": current,
        "has_baseline": baseline_entry is not None,
        "baseline_date": baseline_entry["date"] if baseline_entry else None,
        "volume_change_pct": None,
        "lists": {},
        "coverage_gaps": coverage_gaps(current["coverage"]),
    }
    if not baseline_entry:
        return result

    baseline = baseline_entry[window_key]
    result["volume_change_pct"] = pct_change(current["posting_count"], baseline["posting_count"])
    for key in TRACKED_LISTS:
        result["lists"][key] = rank_changes(current[key], baseline[key])
    return result


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@500;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink: #20242B; --ink-soft: #4B5259; --paper: #FAF7F0; --paper-raised: #FFFFFF;
    --line: #E3DDCE; --navy: #1F3B57; --navy-soft: #2E4B6B; --gold: #B5892B;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--paper); color: var(--ink); font-family: "Source Sans 3", sans-serif; }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 28px 20px 48px; }}
  h1 {{ font-family: "Fraunces", serif; font-weight: 600; font-size: clamp(22px, 4vw, 30px); margin: 0 0 6px; color: var(--navy); }}
  h2 {{ font-family: "Fraunces", serif; font-weight: 600; font-size: 19px; color: var(--ink); margin: 32px 0 10px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
  .sub {{ color: var(--ink-soft); font-size: 14px; margin: 0 0 4px; }}
  .nav-links {{ margin: 14px 0 0; font-size: 13px; }}
  .nav-links a {{ color: var(--navy-soft); text-decoration: none; margin-right: 16px; font-weight: 600; }}
  .nav-links a:hover {{ text-decoration: underline; }}
  .stat-row {{ display: flex; gap: 14px; margin: 18px 0; flex-wrap: wrap; }}
  .stat-tile {{ background: var(--paper-raised); border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px; min-width: 150px; flex: 1; }}
  .stat-tile .num {{ font-family: "Fraunces", serif; font-size: 24px; font-weight: 600; color: var(--navy); line-height: 1.1; }}
  .stat-tile .label {{ font-size: 12.5px; color: var(--ink-soft); margin-top: 3px; }}
  p.lead {{ font-size: 15.5px; line-height: 1.6; }}
  .panel {{ background: var(--paper-raised); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; margin-bottom: 14px; }}
  .panel h3 {{ font-size: 14.5px; font-weight: 700; margin: 0 0 8px; color: var(--navy); }}
  ul.finding-list {{ margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.7; }}
  ul.finding-list li {{ margin-bottom: 4px; }}
  .tag {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 999px; margin-right: 6px; }}
  .tag.up {{ background: #E4EFE6; color: #2E6B3E; }}
  .tag.new {{ background: #EAF0F7; color: var(--navy); }}
  .tag.down {{ background: #F7ECEA; color: #8C3A2E; }}
  .tag.gap {{ background: #FBF1DE; color: var(--gold); }}
  .empty-note {{ font-size: 13.5px; color: var(--ink-soft); font-style: italic; }}
  .method-note {{ font-size: 12px; color: var(--ink-soft); margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--line); line-height: 1.6; }}
  .page-foot {{ font-size: 12px; color: var(--ink-soft); margin-top: 18px; }}
</style>
</head>
<body>
<div class="wrap">
"""

PAGE_TAIL = """
</div>
</body>
</html>
"""


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_list_findings(list_key, changes):
    name = LIST_DISPLAY_NAMES[list_key]
    parts = []
    if changes["persistent"]:
        items = ", ".join(f"<strong>{esc(i['label'])}</strong> ({i['count']})" for i in changes["persistent"][:3])
        parts.append(f"<li><span class=\"tag\" style=\"background:#EFEAE0;color:var(--ink-soft);\">STABLE</span>"
                      f"Consistently near the top across both periods: {items}.</li>")
    if changes["climbers"]:
        items = ", ".join(
            f"<strong>{esc(i['label'])}</strong> (+{i['ranks_up']} ranks, now {i['count']})"
            for i in changes["climbers"][:3]
        )
        parts.append(f"<li><span class=\"tag up\">RISING</span>Climbing fast in {name}: {items}.</li>")
    if changes["new_entrants"]:
        items = ", ".join(f"<strong>{esc(i['label'])}</strong> ({i['count']})" for i in changes["new_entrants"][:3])
        parts.append(f"<li><span class=\"tag new\">NEW</span>Newly appearing in the top {TOP_N_FOR_TRENDS} {name}: {items}.</li>")
    if changes["dropped"]:
        items = ", ".join(f"<strong>{esc(i['label'])}</strong>" for i in changes["dropped"][:3])
        parts.append(f"<li><span class=\"tag down\">FADING</span>Dropped out of the top {TOP_N_FOR_TRENDS} {name}: {items}.</li>")
    return parts


def render_report_html(report_title, period_label, window_label, generated_at, insights, this_url_name, other_report_link, dashboard_link):
    current = insights["current"]
    lead_sentences = []

    if insights["volume_change_pct"] is not None:
        direction = "up" if insights["volume_change_pct"] >= 0 else "down"
        lead_sentences.append(
            f"Tracked postings in this {window_label} window are {direction} "
            f"{abs(insights['volume_change_pct'])}% compared with the equivalent window around "
            f"{esc(insights['baseline_date'])}."
        )
    else:
        lead_sentences.append(
            "This is the first tracked snapshot for this report — there isn't yet an earlier "
            "snapshot far enough back to compare against, so what follows is a point-in-time read "
            "rather than a trend. Trend comparisons will appear automatically once enough history "
            "has accumulated."
        )
    lead_sentences.append(
        f"{current['posting_count']} postings were tracked across all sources in this window."
    )
    lead = " ".join(lead_sentences)

    stat_tiles = f"""
    <div class="stat-row">
      <div class="stat-tile"><div class="num">{current['posting_count']}</div><div class="label">Postings tracked ({window_label})</div></div>
      <div class="stat-tile"><div class="num">{current['top_skills'][0]['label'] if current['top_skills'] else '—'}</div><div class="label">Top requested skill</div></div>
      <div class="stat-tile"><div class="num">{current['top_categories'][0]['label'] if current['top_categories'] else '—'}</div><div class="label">Most active sector</div></div>
      <div class="stat-tile"><div class="num">{current['top_employers'][0]['label'] if current['top_employers'] else '—'}</div><div class="label">Top hiring employer</div></div>
    </div>
    """

    if insights["has_baseline"]:
        findings_html = ""
        for key in TRACKED_LISTS:
            items = render_list_findings(key, insights["lists"][key])
            if items:
                findings_html += f"<div class=\"panel\"><h3>{LIST_DISPLAY_NAMES[key].capitalize()}</h3><ul class=\"finding-list\">{''.join(items)}</ul></div>\n"
        if not findings_html:
            findings_html = "<p class=\"empty-note\">No notable rank changes this period — the mix of top skills, sectors, roles, locations and employers held steady.</p>"
    else:
        findings_html = "<p class=\"empty-note\">Not enough history yet to show persistent or emerging patterns — check back once a few more reporting periods have run.</p>"

    def top_list_html(key, n=8):
        items = current.get(key, [])[:n]
        if not items:
            return "<p class=\"empty-note\">No data yet for this window.</p>"
        rows = "".join(f"<li>{esc(i['label'])} <span style=\"color:var(--ink-soft);\">({i['count']})</span></li>" for i in items)
        return f"<ul class=\"finding-list\">{rows}</ul>"

    gaps = insights["coverage_gaps"]
    if gaps:
        gap_items = "".join(
            f"<li><span class=\"tag gap\">GAP</span>Only {g['pct']}% of postings state "
            f"{esc(g['label'])} ({g['known']} of {g['total']}) — figures for this field should be "
            f"read as a lower bound, not the full picture.</li>"
            for g in gaps
        )
        challenges_html = f"<ul class=\"finding-list\">{gap_items}</ul>"
    else:
        challenges_html = "<p class=\"empty-note\">No major data-coverage gaps flagged for this window — the tracked fields are stated clearly enough in most postings.</p>"

    other_name = "monthly" if "weekly" in this_url_name else "weekly"

    body = f"""
<header>
  <h1>{esc(report_title)}</h1>
  <p class="sub">{esc(period_label)} &middot; Generated {esc(generated_at)}</p>
  <div class="nav-links">
    <a href="{esc(dashboard_link)}">&larr; Back to the live dashboard</a>
    <a href="{esc(other_report_link)}">View the {other_name} report</a>
  </div>
</header>

<p class="lead">{lead}</p>
{stat_tiles}

<h2>Key Findings &mdash; Persistent and Emerging Patterns</h2>
{findings_html}

<h2>Most Requested Skills</h2>
{top_list_html('top_skills')}

<h2>Most Active Sectors</h2>
{top_list_html('top_categories')}

<h2>Top Job Roles</h2>
{top_list_html('top_jobs')}

<h2>Top Locations</h2>
{top_list_html('top_locations')}

<h2>Top Hiring Employers</h2>
{top_list_html('top_employers')}

<h2>Data Challenges &amp; Limitations</h2>
{challenges_html}

<p class="method-note">
  Skills are classified with ESCO, job roles with ISCO-08, education with ISCED 2011 and sectors
  with ISIC Rev.4, the same taxonomies the live dashboard uses. "Persistent" patterns are items
  that stayed in the top 3 of their category across both the current and comparison snapshot.
  "Rising" items climbed at least {CLIMB_THRESHOLD} rank positions within the top {TOP_N_FOR_TRENDS};
  "New" items entered the top {TOP_N_FOR_TRENDS} without having been there before; "Fading" items
  left the top {TOP_N_FOR_TRENDS} entirely. This report compares the current window against the
  closest available snapshot from roughly one {window_label.split()[0]} earlier (within a few days'
  tolerance) &mdash; so it is only as sharp as the daily tracking history behind it, which grows
  more informative the longer the pipeline runs.
</p>
<p class="page-foot">Data from EthioJobs, HaHuJobs, ReporterJobs, PalmJobs, DevNetJobs and HarmeeJobs, tracked daily.</p>
"""
    return PAGE_HEAD.format(title=esc(report_title)) + body + PAGE_TAIL


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    rollups = json.loads(Path(ROLLUPS_PATH).read_text())
    as_of_date = rollups["generated_at"][:10]  # "YYYY-MM-DD HH:MM:SS UTC" -> "YYYY-MM-DD"

    history = update_history(HISTORY_PATH, rollups, as_of_date)
    weekly_baseline = find_baseline(history, as_of_date, target_days_ago=7)
    monthly_baseline = find_baseline(history, as_of_date, target_days_ago=30)

    weekly_insights = build_insights(rollups["windows"]["weekly"], weekly_baseline, "weekly")
    monthly_insights = build_insights(rollups["windows"]["monthly"], monthly_baseline, "monthly")

    weekly_html = render_report_html(
        report_title="Vacancy and Skill Intelligence Report — Weekly",
        period_label=f"Covers the 7 days up to {as_of_date}",
        window_label="week",
        generated_at=rollups["generated_at"],
        insights=weekly_insights,
        this_url_name="weekly-intelligence-report.html",
        other_report_link="monthly-intelligence-report.html",
        dashboard_link="../index.html",
    )
    monthly_html = render_report_html(
        report_title="Vacancy and Skill Intelligence Report — Monthly",
        period_label=f"Covers the 30 days up to {as_of_date}",
        window_label="month",
        generated_at=rollups["generated_at"],
        insights=monthly_insights,
        this_url_name="monthly-intelligence-report.html",
        other_report_link="weekly-intelligence-report.html",
        dashboard_link="../index.html",
    )

    Path(WEEKLY_REPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(WEEKLY_REPORT_PATH).write_text(weekly_html)
    Path(MONTHLY_REPORT_PATH).write_text(monthly_html)
    print(f"Wrote {WEEKLY_REPORT_PATH} and {MONTHLY_REPORT_PATH} "
          f"(history now has {len(history)} daily snapshot(s)).")


if __name__ == "__main__":
    main()
