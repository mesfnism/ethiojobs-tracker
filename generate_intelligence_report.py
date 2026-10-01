"""
EthioSkills Intelligence — Vacancy and Skill Intelligence Report.

Turns the same rollups.json the dashboard reads into two full analytical
reports: a weekly one (the rolling 7-day window) and a monthly one (the
rolling 30-day window). Each report is written as a structured research
brief rather than a dashboard screenshot — cover page, table of contents,
executive summary, introduction, data sources (with their strengths,
weaknesses and possible fixes), an analysis section with tables and simple
figures, a conclusion, recommendations, a stated-limitations section, and
a references page with numbered citations. Author and contact details are
fixed to the report's owner (see AUTHOR_* below).

Each report is produced in two formats:
  - docs/reports/<window>-intelligence-report.html  (published on GitHub
    Pages, linkable and shareable directly)
  - docs/reports/<window>-intelligence-report.pdf    (same content,
    paginated for print/attachment use), rendered from the HTML using the
    Chromium browser Playwright already installs for the scraping
    pipelines — no extra system dependency.

How the "what changed" comparison works
----------------------------------------
rollups.json only ever holds the CURRENT state. To know what is trending,
the script keeps a small running history file, docs/data/report_history.json
— one compact entry per day this script has run. Every run appends today's
snapshot, trims the history to the most recent ~400 entries, and looks back
for the entry closest to 7 (or 30) days ago to use as a comparison baseline.
The very first time this runs there is nothing to compare against yet, and
the report says so plainly rather than inventing a trend.

Run this AFTER rollup_ethiojobs.py in the same workflow, since it reads
rollup_ethiojobs.py's own output (docs/data/rollups.json).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROLLUPS_PATH = "docs/data/rollups.json"
HISTORY_PATH = "docs/data/report_history.json"
REPORTS_DIR = "docs/reports"

PROJECT_NAME = "EthioSkills Intelligence"
AUTHOR_NAME = "Mesfin Abraham Ali"
AUTHOR_CONTACT = "mesfnism@gmail.com"
COLLABORATION_NOTE = (
    "This report is produced and maintained independently by " + AUTHOR_NAME + ". "
    "Comments, corrections, proposals to share data, and collaboration or funding "
    "inquiries are welcome at " + AUTHOR_CONTACT + ". The underlying pipeline is "
    "built to scale to additional vacancy sources, additional countries in the "
    "Horn of Africa, and partnerships with official statistical and labour market "
    "information bodies."
)

HISTORY_MAX_ENTRIES = 400
TOP_N_FOR_TRENDS = 10
CLIMB_THRESHOLD = 2          # rank positions moved up to count as a "climber"
COVERAGE_CONCERN_PCT = 50    # a field known for under this % of postings is flagged as a data gap

# Recorded on every edition so that a later change in skill or sector counts
# can be told apart from a change in how the classifier works, per the
# project critique's point that "international taxonomies" alone do not
# guarantee comparability over time, stable classifier logic does. Bump
# CLASSIFIER_VERSION by hand whenever the keyword rules or category
# mappings in taxonomy.py or rollup_ethiojobs.py change in a way that could
# shift skill, education or sector counts on its own, independent of any
# real change in the underlying postings.
CLASSIFIER_VERSION = "v1.0"
TAXONOMY_VERSIONS = "ESCO groups (own keyword mapping), ISCO-08, ISCED 2011, ISIC Rev.4"

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

# Numbered references used (via [n] markers) across the narrative sections.
REFERENCES = [
    "International Labour Organization (2023). Comprehensive Assessment of the State "
    "of the Labour Market and Migration Information System in Ethiopia. ILO BRMM "
    "Programme, funded by the UK Foreign, Commonwealth and Development Office (FCDO).",
    "International Labour Organization (2026). “Ethiopia and South Africa Share "
    "Lessons on Labour Market Information Systems.” ILO News, 20 April 2026. "
    "https://www.ilo.org/resource/article/ethiopia-and-south-africa-share-lessons-labour-market-information-systems-0",
    "World Bank (2023). Ethiopia Education and Skills for Employability Project. "
    "Project Appraisal Document, Component 3, System Strengthening.",
    "European Commission. ESCO (European Skills, Competences, Qualifications and "
    "Occupations), a skills taxonomy. https://esco.ec.europa.eu",
    "International Labour Organization. ISCO-08 (International Standard "
    "Classification of Occupations). https://www.ilo.org/public/english/bureau/stat/isco/isco08/",
    "UNESCO Institute for Statistics. ISCED 2011 (International Standard "
    "Classification of Education).",
    "United Nations Statistics Division. ISIC Rev.4 (International Standard "
    "Industrial Classification of All Economic Activities).",
    "Primary vacancy data. EthioJobs (ethiojobs.net), HaHuJobs (hahu.jobs), Ethiopian "
    "Reporter Jobs (ethiopianreporterjobs.com), PalmJobs (palmjobs.et), DevNetJobs "
    "(devnetjobs.org, filtered to Ethiopia) and HarmeeJobs, scraped and standardized "
    "daily by the author's own open pipeline (github.com, EthioJobs Tracker project).",
]

# Per-source notes: scope, strength, weakness, possible fix. Used in the Sources page.
SOURCE_PROFILES = [
    {
        "name": "EthioJobs", "key": "EthioJobs",
        "scope": "Ethiopia's largest general job board, spanning private companies, "
                 "banks, manufacturers, schools and some NGOs.",
        "strength": "the largest and steadiest volume of any single source tracked here, "
                    "with structured fields (career level, education, years of experience, "
                    "required skills) read directly from each posting's detail page rather "
                    "than inferred.",
        "weakness": "postings rarely state a wage figure, so salary coverage for this "
                    "source alone is thin, and a posting written only in Amharic is not "
                    "yet parsed into the skills, education or sector classifiers.",
        "fix": "extending the skills and category classifiers to Amharic text, and "
               "cross-checking a sample of postings against the employer's own site for "
               "wage information where available.",
    },
    {
        "name": "HaHuJobs", "key": "HaHuJobs",
        "scope": "A second major Ethiopian job board, covering a mix of employers "
                 "similar to EthioJobs in the private sector, with heavier "
                 "representation from public institutions and universities.",
        "strength": "job descriptions written as free text that, unlike EthioJobs, "
                    "often let the field of study and salary classifiers extract a "
                    "specific value rather than only a coarse category.",
        "weakness": "a meaningful share of its postings from the public sector and "
                    "universities are written entirely in Amharic, which the current "
                    "classifiers cannot read. Those postings count toward total volume "
                    "but not toward the Skills, Education or Sector breakdowns.",
        "fix": "extending language coverage to Amharic, the same fix planned for "
               "EthioJobs, would recover this gap directly, since the underlying "
               "postings already exist in the tracked data.",
    },
    {
        "name": "Ethiopian Reporter Jobs", "key": "ReporterJobs",
        "scope": "The vacancy section of an Ethiopian weekly newspaper that has run "
                 "for many years. It carries a distinct mix of postings, with a "
                 "larger share of public sector and NGO notices than the job boards "
                 "that operate purely online.",
        "strength": "being independent of the online job boards, so it surfaces "
                    "vacancies, particularly notices from public institutions, that "
                    "may not be posted anywhere else.",
        "weakness": "the site runs bot detection that can intermittently block an "
                    "automated browser. As of this report its tracked volume is "
                    "noticeably thinner than the other sources. This is a known and "
                    "acknowledged gap rather than a silent one (see Limitations).",
        "fix": "a fix to the browser's fingerprint has already been applied. "
               "Confirming it holds across multiple scheduled runs, and adding an "
               "alert when a run returns zero postings, are the next concrete steps.",
    },
    {
        "name": "PalmJobs", "key": "PalmJobs",
        "scope": "A newer Ethiopian job board drawing on a modern web stack, "
                 "covering a mix of employers broadly similar to EthioJobs and "
                 "HaHuJobs in the private sector.",
        "strength": "structured data read directly from the site's own API, giving "
                    "clean fields with little parsing of free text required once the "
                    "connection to that API is working.",
        "weakness": "the site has changed its own technical architecture more than "
                    "once since tracking began, each time silently breaking the "
                    "pipeline's connection to it until corrected. Its tracked volume "
                    "is currently thinner than it should be as a result.",
        "fix": "a fix that reads the site's own public API key directly from its "
               "code has been applied. The next scheduled runs will show whether it "
               "restores full coverage, and a lightweight daily check, asking "
               "whether this source returned zero postings today, is planned to "
               "catch the next such change quickly.",
    },
    {
        "name": "DevNetJobs", "key": "DevNetJobs",
        "scope": "An international vacancy board for the development sector. This "
                 "pipeline filters it to postings relevant to Ethiopia only, using a "
                 "keyword search, so it is deliberately narrower than the general "
                 "job boards.",
        "strength": "a distinct slice of the market oriented toward international "
                    "work, donor roles, NGO roles and development programme roles, "
                    "that general job boards rarely carry.",
        "weakness": "resolving a listing into its full detail page depends on "
                    "replaying the site's own interactive behaviour, which is fragile "
                    "by nature, and a share of its postings are visible to members "
                    "only and are correctly skipped rather than guessed at. Both "
                    "factors currently keep its tracked volume very low. As of this "
                    "report, it is effectively a single posting.",
        "fix": "the pipeline now drives that interaction through a real browser "
               "page rather than approximating it, which should recover most "
               "postings that were previously missed, on the next run. If volume "
               "stays low after that, the keyword search itself, currently scoped "
               "narrowly to avoid noise from listings visible only to members, is "
               "the next thing to widen.",
    },
    {
        "name": "HarmeeJobs", "key": "HarmeeJobs",
        "scope": "An Ethiopian job board read via its own public RSS feed rather than by "
                 "parsing rendered HTML.",
        "strength": "the cleanest and most stable connection of any source tracked here, "
                    "since an RSS feed is a stable, structured contract that a site "
                    "redesign is unlikely to break.",
        "weakness": "an RSS feed typically carries less structured detail per posting "
                    "than a full detail page, so some fields (years of experience, a "
                    "specific salary figure) are stated less often here than for sources "
                    "read from full detail pages.",
        "fix": "where the feed links back to a full posting page, fetching that page for "
               "the subset of fields it still lacks is a natural next step.",
    },
]


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
    target. Never matches today's own just-appended entry."""
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
    sorts each into: persistent, climbers, new_entrants, dropped."""
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
    return {"persistent": persistent, "climbers": climbers, "new_entrants": new_entrants, "dropped": dropped}


def coverage_gaps(coverage):
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
# Small inline figures (no charting library — dependency-free SVG)
# ---------------------------------------------------------------------------

def bar_chart_svg(items, value_key="count", label_key="label", width=600, bar_height=22, gap=8,
                   color="#1F3B57", max_items=8):
    items = items[:max_items]
    if not items:
        return ""
    max_val = max(i[value_key] for i in items) or 1
    label_col = 190
    chart_w = width - label_col - 50
    rows = []
    y = 0
    for item in items:
        frac = item[value_key] / max_val
        bar_w = max(2, round(frac * chart_w))
        label = esc(item[label_key])
        if len(label) > 26:
            label = label[:24] + "…"
        rows.append(
            f'<text x="{label_col - 8}" y="{y + bar_height * 0.68}" text-anchor="end" '
            f'font-size="12" font-family="Source Sans 3, sans-serif" fill="#20242B">{label}</text>'
            f'<rect x="{label_col}" y="{y}" width="{bar_w}" height="{bar_height - 6}" rx="3" fill="{color}"/>'
            f'<text x="{label_col + bar_w + 6}" y="{y + bar_height * 0.68}" '
            f'font-size="12" font-family="Source Sans 3, sans-serif" fill="#4B5259">{item[value_key]}</text>'
        )
        y += bar_height + gap
    total_h = y
    return (f'<svg viewBox="0 0 {width} {total_h}" width="100%" height="{total_h}" '
            f'role="img" aria-label="bar chart">{"".join(rows)}</svg>')


# ---------------------------------------------------------------------------
# HTML shell
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
  .page {{ max-width: 860px; margin: 0 auto; padding: 48px 36px; page-break-after: always; }}
  .page:last-of-type {{ page-break-after: auto; }}
  .cover {{ min-height: 90vh; display: flex; flex-direction: column; justify-content: center; }}
  .cover .kicker {{ font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--navy-soft); font-weight: 700; margin-bottom: 18px; }}
  .cover h1 {{ font-family: "Fraunces", serif; font-weight: 700; font-size: clamp(28px, 5vw, 42px); color: var(--navy); margin: 0 0 10px; line-height: 1.15; }}
  .cover .period {{ font-size: 16px; color: var(--ink-soft); margin-bottom: 36px; }}
  .cover .meta-row {{ border-top: 1px solid var(--line); padding-top: 18px; font-size: 13.5px; color: var(--ink-soft); line-height: 1.8; }}
  .cover .meta-row strong {{ color: var(--ink); }}
  .cover .collab {{ margin-top: 28px; font-size: 13px; color: var(--ink-soft); line-height: 1.7; max-width: 560px; }}
  h1.page-title {{ font-family: "Fraunces", serif; font-weight: 600; font-size: 26px; color: var(--navy); margin: 0 0 22px; border-bottom: 2px solid var(--navy); padding-bottom: 12px; }}
  h2 {{ font-family: "Fraunces", serif; font-weight: 600; font-size: 18px; color: var(--ink); margin: 26px 0 10px; }}
  h3 {{ font-size: 14.5px; font-weight: 700; color: var(--navy); margin: 18px 0 8px; }}
  p {{ font-size: 14.5px; line-height: 1.7; margin: 0 0 12px; text-align: justify; }}
  .toc ol {{ font-size: 15px; line-height: 2.1; padding-left: 20px; }}
  .toc a {{ color: var(--ink); text-decoration: none; }}
  .toc a:hover {{ text-decoration: underline; }}
  .stat-row {{ display: flex; gap: 14px; margin: 18px 0 20px; flex-wrap: wrap; }}
  .stat-tile {{ background: var(--paper-raised); border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px; min-width: 150px; flex: 1; }}
  .stat-tile .num {{ font-family: "Fraunces", serif; font-size: 22px; font-weight: 600; color: var(--navy); line-height: 1.1; }}
  .stat-tile .label {{ font-size: 12px; color: var(--ink-soft); margin-top: 3px; }}
  table.data-table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; margin: 10px 0 18px; }}
  table.data-table caption {{ text-align: left; font-size: 12.5px; color: var(--ink-soft); font-style: italic; margin-bottom: 6px; caption-side: top; }}
  table.data-table th {{ text-align: left; border-bottom: 2px solid var(--navy); padding: 6px 10px; color: var(--navy); font-weight: 700; }}
  table.data-table td {{ border-bottom: 1px solid var(--line); padding: 6px 10px; }}
  table.data-table tr:last-child td {{ border-bottom: 1px solid var(--line); }}
  figure {{ margin: 10px 0 20px; }}
  figcaption {{ font-size: 12.5px; color: var(--ink-soft); font-style: italic; margin-top: 6px; }}
  .source-block {{ margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }}
  .source-block h3 {{ margin-bottom: 4px; }}
  .tag {{ display: inline-block; font-size: 10.5px; font-weight: 700; padding: 1px 7px; border-radius: 999px; margin-right: 6px; vertical-align: 1px; }}
  .tag.up {{ background: #E4EFE6; color: #2E6B3E; }}
  .tag.new {{ background: #EAF0F7; color: var(--navy); }}
  .tag.down {{ background: #F7ECEA; color: #8C3A2E; }}
  .tag.gap {{ background: #FBF1DE; color: var(--gold); }}
  .tag.stable {{ background: #EFEAE0; color: var(--ink-soft); }}
  ul.plain {{ font-size: 14.5px; line-height: 1.8; padding-left: 20px; margin: 0 0 14px; }}
  .scope-box {{ background: var(--paper-raised); border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px 4px; margin: 4px 0 20px; }}
  .scope-box h3 {{ margin-top: 0; font-size: 14.5px; }}
  .scope-cols {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .scope-cols > div {{ flex: 1; min-width: 200px; }}
  .scope-head {{ font-weight: 700; font-size: 12.5px; color: var(--navy); margin: 0 0 4px; text-transform: uppercase; letter-spacing: 0.03em; }}
  .scope-box ul.plain {{ font-size: 13px; margin-bottom: 10px; }}
  ol.ref-list {{ font-size: 13px; line-height: 1.9; padding-left: 20px; }}
  ol.ref-list li {{ margin-bottom: 10px; }}
  .page-foot {{ font-size: 11.5px; color: var(--ink-soft); margin-top: 30px; padding-top: 12px; border-top: 1px solid var(--line); }}
  .nav-links {{ font-size: 12.5px; margin-top: 4px; }}
  .nav-links a {{ color: var(--navy-soft); text-decoration: none; margin-right: 16px; font-weight: 600; }}
  @media print {{
    body {{ background: #fff; }}
    .page {{ padding: 24px 6px; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>
"""

PAGE_TAIL = """
</body>
</html>
"""


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def page(content_html, extra_class=""):
    return f'<div class="page {extra_class}">{content_html}</div>'


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def build_cover(report_title, window_label, period_label, generated_at, other_report_link, dashboard_link):
    return page(f"""
<div class="kicker">{esc(PROJECT_NAME)}</div>
<h1>{esc(report_title)}</h1>
<p class="period">{esc(period_label)}</p>
<div class="meta-row">
  <div><strong>Author and Project Owner</strong> {esc(AUTHOR_NAME)}</div>
  <div><strong>Contact</strong> {esc(AUTHOR_CONTACT)}</div>
  <div><strong>Generated</strong> {esc(generated_at)}</div>
  <div><strong>Classifier version</strong> {esc(CLASSIFIER_VERSION)}</div>
  <div><strong>Taxonomies applied</strong> {esc(TAXONOMY_VERSIONS)}</div>
  <div class="nav-links">
    <a href="{esc(dashboard_link)}">Live dashboard &rarr;</a>
    <a href="{esc(other_report_link)}">Companion report &rarr;</a>
  </div>
</div>
<p class="collab">{esc(COLLABORATION_NOTE)}</p>
""", "cover")


def build_toc():
    items = [
        ("exec-summary", "Executive Summary"),
        ("introduction", "Introduction"),
        ("sources", "Data Sources"),
        ("analysis", "Analysis"),
        ("limitations", "Advantages, Limitations and Planned Improvements"),
        ("conclusion", "Conclusion"),
        ("recommendations", "Recommendations"),
        ("references", "References"),
    ]
    rows = "".join(f'<li><a href="#{anchor}">{esc(title)}</a></li>' for anchor, title in items)
    return page(f'<h1 class="page-title">Contents</h1><div class="toc"><ol>{rows}</ol></div>')


def build_executive_summary(window_label, period_label, current, insights):
    top_skill = current["top_skills"][0]["label"] if current["top_skills"] else "no single skill"
    top_sector = current["top_categories"][0]["label"] if current["top_categories"] else "no single sector"
    top_employer = current["top_employers"][0]["label"] if current["top_employers"] else "no single employer"

    if insights["volume_change_pct"] is not None:
        direction = "an increase" if insights["volume_change_pct"] >= 0 else "a decrease"
        trend_sentence = (
            f"Relative to the comparable {window_label} window around "
            f"{esc(insights['baseline_date'])}, tracked volume shows {direction} of "
            f"{abs(insights['volume_change_pct'])} percent, which this report treats as an "
            f"indicative signal of market movement in the short run rather than a precise "
            f"measure of total vacancy creation in the economy."
        )
    else:
        trend_sentence = (
            "This is the first snapshot recorded for this reporting cycle, so a "
            "comparison from one week to the next, or one month to the next, is not "
            "yet possible. Subsequent editions of this report will carry trend "
            "comparisons as tracking history accumulates."
        )

    stat_tiles = f"""
    <div class="stat-row">
      <div class="stat-tile"><div class="num">{current['posting_count']}</div><div class="label">Postings tracked ({window_label})</div></div>
      <div class="stat-tile"><div class="num">{esc(top_skill)}</div><div class="label">Most frequently mentioned skill</div></div>
      <div class="stat-tile"><div class="num">{esc(top_sector)}</div><div class="label">Leading economic activity (ISIC)</div></div>
      <div class="stat-tile"><div class="num">{esc(top_employer)}</div><div class="label">Employer with the most postings</div></div>
    </div>
    """

    scope_box = """
    <div class="scope-box">
      <h3>What this report measures, and what it does not</h3>
      <div class="scope-cols">
        <div>
          <p class="scope-head">It measures</p>
          <ul class="plain">
            <li>Online vacancy advertisements on six tracked job boards</li>
            <li>Skills, qualifications and experience as advertised in those postings</li>
            <li>Employer and economic activity patterns within that advertised set</li>
            <li>Period to period change in what is advertised online</li>
          </ul>
        </div>
        <div>
          <p class="scope-head">It does not measure</p>
          <ul class="plain">
            <li>Total vacancies or total labour demand in Ethiopia</li>
            <li>Unemployment or skill shortages</li>
            <li>Whether a posting results in an actual hire</li>
            <li>Informal or unadvertised recruitment</li>
          </ul>
        </div>
      </div>
    </div>
    """

    body = f"""
<h1 class="page-title" id="exec-summary">Executive Summary</h1>
<p>This {window_label} edition of {esc(PROJECT_NAME)} tracked {current['posting_count']} vacancy
postings across six Ethiopian job boards, standardized against international occupation, skills,
education and industry taxonomies so that figures reported here are comparable across sources and
over time. The purpose of this exercise is not to replace Ethiopia's official labour market
information architecture, but to complement it. The national E-LMIS and the Ethiopian Statistical
Service's household and establishment surveys provide periodic, authoritative but infrequent
measurement of the labour market [1][3]. This report offers a continuously updated, narrower read
of what employers are actually advertising for, close to real time. It tracks advertised demand.
It does not measure labour shortages, unemployment or actual hiring outcomes, which require the
survey and administrative sources cited above.</p>
<p>The most frequently mentioned skill this period was <strong>{esc(top_skill)}</strong>, the
leading economic activity by posting count was <strong>{esc(top_sector)}</strong>, and the employer
with the most identifiable postings was <strong>{esc(top_employer)}</strong>. A posting mentioning a
skill is not by itself evidence that the skill is scarce or that employers struggle to find it.
{trend_sentence} The Analysis section below sets out these patterns in full, with supporting tables
and figures. The Data Sources and Limitations sections set out, without qualification, what this
exercise can and cannot yet claim to measure.</p>
{stat_tiles}
{scope_box}
"""
    return page(body)


def build_introduction(window_label, period_label):
    body = f"""
<h1 class="page-title" id="introduction">Introduction</h1>
<p>Ethiopia's labour market is widely characterised, in both the academic and policy literature, by
high youth unemployment, informality, and a persistent mismatch between the skills job seekers hold
and the skills employers require [1][3]. A recurring theme in assessments of the country's labour
market information architecture is fragmentation. Multiple institutions, including the Ministry of
Labour and Skills, the Ethiopian Statistical Service, the Ethiopian Investment Commission, and
programme offices funded by donors, each hold partial data collected administratively, with limited
interoperability or routine sharing of data between them [1][2]. Ethiopia's own E-LMIS initiative,
evolving through systems including Ethioworks and JEDI, is a direct institutional response to this
gap, but remains a work in progress [1][2].</p>
<p>{esc(PROJECT_NAME)} takes a narrower, complementary approach. Rather than attempting to measure
the labour market as a whole, it tracks what is observable directly and continuously, namely vacancy
postings advertised on Ethiopia's most used online job boards. Each posting is read, structured, and
classified against four international standards. ESCO for skills, ISCO-08 for occupations, ISCED
2011 for education levels, and ISIC Rev.4 for industry sectors [4][5][6][7]. This makes a posting's
requirements comparable across sources and legible to anyone already familiar with those schemes,
including statistical agencies and development partners.</p>
<p>This {window_label} report covers {esc(period_label).lower()}. It is one of two companion editions
produced on a rolling basis. A weekly edition captures movement over the short run, and a monthly edition
captures a steadier baseline. Both are generated automatically from the same underlying dataset and
published alongside a live, continuously updated dashboard.</p>
<h2>Scope and methodology, briefly</h2>
<p>Six sources are tracked. EthioJobs, HaHuJobs, Ethiopian Reporter Jobs, PalmJobs, DevNetJobs
(filtered to postings relevant to Ethiopia) and HarmeeJobs. A posting that has been cross posted to
more than one source is identified and merged rather than double counted, matched on employer, job
title and location within a short window around the posting date. Each of the four taxonomies above
is applied independently per field. A posting that cannot be confidently classified against a given
taxonomy is excluded from that specific breakdown rather than guessed at, and is still counted
toward overall volume. The Data Sources section that follows sets out what each source does and
does not capture well. The Limitations section sets out what this leaves unresolved.</p>
"""
    return page(body)


def build_sources(insights):
    gap_keys = {g["field"] for g in insights["coverage_gaps"]}
    blocks = []
    for s in SOURCE_PROFILES:
        blocks.append(f"""
<div class="source-block">
  <h3>{esc(s['name'])}</h3>
  <p><strong>Scope.</strong> {esc(s['scope'])}</p>
  <p><strong>Strength.</strong> Its principal advantage in this dataset is {esc(s['strength'])}</p>
  <p><strong>Weakness.</strong> Its main limitation is that {esc(s['weakness'])}</p>
  <p><strong>Possible improvement.</strong> {esc(s['fix'])}</p>
</div>""")
    gap_note = ""
    if gap_keys:
        fields = ", ".join(COVERAGE_FIELDS[k] for k in gap_keys)
        gap_note = (f"<p>Separately from coverage at the source level, this period's data also shows "
                    f"thin coverage on fields that depend on a posting stating them explicitly. Fewer "
                    f"than {COVERAGE_CONCERN_PCT} percent of postings report {esc(fields)}. This is a "
                    f"property of what employers choose to disclose, not a parsing failure, and it is "
                    f"treated as a stated limitation throughout rather than estimated or imputed.</p>")
    body = f"""
<h1 class="page-title" id="sources">Data Sources</h1>
<p>Each source below is described in terms of what it covers, what it is reliably good for, where it
currently falls short, and what a realistic next step to improve it looks like. None of the sources
is treated as authoritative on its own. The value of combining them is in triangulating signal across
boards that would otherwise sit in isolation from each other, in the same spirit recommended for
Ethiopia's wider system of labour market information [1].</p>
{''.join(blocks)}
{gap_note}
"""
    return page(body)


def _list_html_sentence(items, key="label"):
    return ", ".join(f"<strong>{esc(i[key])}</strong>" for i in items)


def build_analysis(window_label, current, insights, window_data):
    skills_table_rows = "".join(
        f"<tr><td>{i + 1}</td><td>{esc(s['label'])}</td><td>{s['count']}</td></tr>"
        for i, s in enumerate(current["top_skills"][:10])
    )
    skills_table = (
        f'<table class="data-table"><caption>Table 1. Most frequently mentioned skills, {esc(window_label)} window.</caption>'
        f'<thead><tr><th>#</th><th>Skill (normalized to ESCO)</th><th>Postings mentioning it</th></tr></thead>'
        f'<tbody>{skills_table_rows}</tbody></table>'
    ) if skills_table_rows else "<p><em>Not enough postings with stated skills yet to tabulate.</em></p>"

    skills_fig = bar_chart_svg(current["top_skills"], max_items=8)
    skills_figure = (
        f'<figure>{skills_fig}<figcaption>Figure 1. Posting counts for the most frequently mentioned skills, {esc(window_label)} window.</figcaption></figure>'
    ) if skills_fig else ""

    # Sector x top critical skill table, using the full window payload (not the condensed insight).
    # Labelled as economic activity (ISIC), not occupation, since the two are different things and a
    # posting's employer-level industry is not a stand-in for the role being advertised.
    sector_rows = []
    crit_by_sector = window_data.get("critical_skills_by_sector", {})
    for sec in window_data.get("top_categories", [])[:8]:
        label = sec["label"]
        entry = crit_by_sector.get(label)
        top_skill = entry["skills"][0]["label"] if entry and entry.get("skills") else "Not enough postings with stated skills"
        sector_rows.append(f"<tr><td>{esc(label)}</td><td>{sec['count']}</td><td>{esc(top_skill)}</td></tr>")
    sector_table = (
        f'<table class="data-table"><caption>Table 2. Economic activities represented among online postings (ISIC), with the skill most often mentioned within each, {esc(window_label)} window.</caption>'
        f'<thead><tr><th>Economic activity (ISIC Rev.4)</th><th>Postings</th><th>Most frequently mentioned skill within it</th></tr></thead>'
        f'<tbody>{"".join(sector_rows)}</tbody></table>'
    ) if sector_rows else "<p><em>Not enough classified sector data yet to tabulate.</em></p>"

    # Narrative on persistence / emergence
    pattern_paras = []
    if insights["has_baseline"]:
        for key in TRACKED_LISTS:
            changes = insights["lists"][key]
            name = LIST_DISPLAY_NAMES[key]
            sentence_parts = []
            if changes["persistent"]:
                sentence_parts.append(
                    f'<span class="tag stable">STABLE</span>{_list_html_sentence(changes["persistent"][:3])} '
                    f"remained near the top of {name} across both periods, indicating a structural rather "
                    f"than seasonal pattern."
                )
            if changes["climbers"]:
                items = ", ".join(f"<strong>{esc(i['label'])}</strong> (up {i['ranks_up']} ranks)" for i in changes["climbers"][:3])
                sentence_parts.append(f'<span class="tag up">RISING</span>{items} climbed in {name}, worth monitoring for whether the movement continues into the next reporting cycle.')
            if changes["new_entrants"]:
                sentence_parts.append(f'<span class="tag new">NEW</span>{_list_html_sentence(changes["new_entrants"][:3])} entered the top {TOP_N_FOR_TRENDS} {name} for the first time this period.')
            if changes["dropped"]:
                sentence_parts.append(f'<span class="tag down">FADING</span>{_list_html_sentence(changes["dropped"][:3])} dropped out of the top {TOP_N_FOR_TRENDS} {name}.')
            if sentence_parts:
                pattern_paras.append(f"<p>{' '.join(sentence_parts)}</p>")
        if not pattern_paras:
            pattern_paras.append("<p>No notable rank movement was detected this period across skills, sectors, roles, locations or employers. The composition of demand held steady relative to the comparison snapshot.</p>")
    else:
        pattern_paras.append(
            "<p>No prior snapshot is yet available far enough back to compare against, so this edition "
            "reports levels rather than change. Persistence and emergence will be reported from the next "
            "cycle in which a comparable baseline exists.</p>"
        )

    edu = window_data.get("education", {})
    exp = window_data.get("experience", {})
    sal = window_data.get("salary", {})
    coverage_sentence = (
        f"Of {current['posting_count']} postings this period, {edu.get('known_total', 0)} "
        f"({round(100 * edu.get('known_total', 0) / edu['total'], 1) if edu.get('total') else 0}%) state a "
        f"clear education requirement, {exp.get('known_total', 0)} "
        f"({round(100 * exp.get('known_total', 0) / exp['total'], 1) if exp.get('total') else 0}%) state years "
        f"of experience required, and only {sal.get('known_total', 0)} "
        f"({round(100 * sal.get('known_total', 0) / sal['total'], 1) if sal.get('total') else 0}%) state a clear "
        f"monthly pay figure. This is consistent with the broader observation that Ethiopian employers "
        f"disclose compensation far less often than qualifications."
    )

    body = f"""
<h1 class="page-title" id="analysis">Analysis</h1>
<h2>Most frequently mentioned skills</h2>
<p>A skill mentioned often in postings is not, by itself, a skill employers cannot find. Generic
transversal skills such as communication are routinely written into job advertisements regardless of
how hard the role is to fill, so the counts below describe what is advertised, not what is scarce.</p>
<p>{skills_table}</p>
{skills_figure}
<h2>Where those mentions cluster, patterns by economic activity</h2>
<p>Which skill is mentioned most often varies by the employer's economic activity. Table 2 reports,
for each of the economic activities with the most postings this period, the single skill most
frequently mentioned within postings tied to that activity, subject to a minimum of three postings
in that activity stating explicit skills, so that a single posting is never reported as an activity's
standard. An economic activity (ISIC) describes the employer's industry, not the occupation being
advertised, and the two should not be read as interchangeable.</p>
{sector_table}
<h2>Education, experience and pay profile</h2>
<p>{coverage_sentence}</p>
<h2>Persistent and emerging patterns</h2>
<p>Comparing this period against the closest available prior snapshot distinguishes mentions that
recur across periods from ones that are newly rising or fading. A single snapshot on its own cannot
make that distinction, and a recurring mention still describes what is advertised, not a confirmed
shortage or structural need.</p>
{''.join(pattern_paras)}
"""
    return page(body)


def build_limitations(insights):
    gaps = insights["coverage_gaps"]
    gap_sentence = ""
    if gaps:
        items = ", ".join(f"{esc(g['label'])} ({g['pct']}% of postings)" for g in gaps)
        gap_sentence = (f"<p>In this edition specifically, coverage is thin for {items}. Three of the six "
                         f"tracked sources, Ethiopian Reporter Jobs, PalmJobs and DevNetJobs, are also "
                         f"currently contributing fewer postings than their true listing volume, for the "
                         f"technical reasons specific to each source set out on the Data Sources page. "
                         f"Fixes for each have been applied and their effect is being confirmed over the "
                         f"next few scheduled runs.</p>")
    body = f"""
<h1 class="page-title" id="limitations">Advantages, Limitations and Planned Improvements</h1>
<h2>Advantages of this approach</h2>
<ul class="plain">
  <li>Continuous, low cost measurement that refreshes automatically, in contrast to periodic labour
  market information based on surveys that updates only every few years.</li>
  <li>Standardized against the same international taxonomies (ESCO, ISCO-08, ISCED 2011, ISIC Rev.4)
  used in official reporting on labour markets, so figures here are directly comparable to other work
  using the same schemes.</li>
  <li>Full transparency on what is and is not captured. A field that cannot be classified confidently
  is excluded from that breakdown rather than estimated, and every honesty threshold used in this
  report (for example a minimum posting count before a skill is reported as most requested for a
  group) is stated rather than hidden.</li>
</ul>
<h2>Limitations</h2>
<ul class="plain">
  <li>Coverage is restricted to vacancies advertised on the specific online boards tracked. Informal,
  word of mouth, and unadvertised hiring, a substantial share of actual hiring activity in Ethiopia,
  is not observed at all.</li>
  <li>Classification currently works on text written in English. A meaningful share of postings,
  particularly from public institutions, are written in Amharic and are not yet classified into the
  Skills, Education or Sector breakdowns, though they are still counted toward overall volume.</li>
  <li>Employer type (Private, Public or NGO) is inferred from a keyword heuristic on the employer's own
  name, not a verified registry lookup, and should be read as indicative.</li>
  <li>Tracked history is still short relative to the seasonal cycles a labour market can exhibit over a
  full year. Early trend comparisons should be read cautiously until more history accumulates.</li>
</ul>
{gap_sentence}
<h2>Planned improvements</h2>
<p>The priorities for the near future are extending the language coverage of the skills and sector classifiers to
Amharic text, closing the volume gap on the three sources that currently fall short, and adding
additional sources, including, subject to feasibility and permission, sources beyond Ethiopia's
borders within the Horn of Africa. Readers who hold data, funding, or institutional mandate relevant
to any of these priorities are invited to make contact (see the cover page for details).</p>
"""
    return page(body)


def build_conclusion(window_label, current, insights):
    top_skill = current["top_skills"][0]["label"] if current["top_skills"] else "no single skill"
    top_sector = current["top_categories"][0]["label"] if current["top_categories"] else "no single sector"

    if insights["has_baseline"]:
        pattern_sentence = (
            f"This concentration, together with the recurrence observed across reporting periods for a "
            f"stable set of top mentioned skills and economic activities, is consistent with a set of "
            f"advertised requirements that holds steady from one period to the next rather than shifting "
            f"unpredictably. That pattern describes what is advertised, not a confirmed shortage, and it "
            f"still has implications for how training providers and jobseekers might prioritise their "
            f"effort."
        )
    else:
        pattern_sentence = (
            "This edition establishes a baseline. It does not yet show whether this concentration holds "
            "from one period to the next, since no prior snapshot exists for comparison. Subsequent "
            "editions will show whether the same skills and economic activities recur or whether this "
            "snapshot reflects a temporary pattern."
        )

    body = f"""
<h1 class="page-title" id="conclusion">Conclusion</h1>
<p>Over this {window_label} window, Ethiopia's tracked online vacancy market concentrated around a
recognisable core of mentioned skills and economic activity, led by {esc(top_skill)} as the most
frequently mentioned skill and {esc(top_sector)} as the economic activity with the most postings.
{pattern_sentence}</p>
<p>At the same time, the gaps documented in this report, thin wage disclosure, uneven coverage across
sources, and a language barrier on postings written only in Amharic, mean that this report's picture
of the market, while directionally useful, remains partial. It is offered as a complement to, not a
substitute for, Ethiopia's official labour market information systems, and is intended to improve in
coverage and depth with each future edition.</p>
"""
    return page(body)


def build_recommendations():
    body = """
<h1 class="page-title" id="recommendations">Recommendations</h1>
<h2>For training providers and TVET institutions</h2>
<p>Align curriculum updates with the skills and sectors shown as persistent in this report's Analysis
section, since persistence across reporting periods is a stronger signal of durable demand than a
single period's figures alone. Where a specific sector's leading skill (Table 2) differs from what a
programme currently emphasises, that gap is worth investigating directly with employers in that
sector.</p>
<h2>For policymakers and labour market information stakeholders</h2>
<p>This report's granularity at the sector and skill level, refreshed continuously, is offered as a
complementary input to the periodic measurement based on surveys that Ethiopia's official labour
market information architecture already produces, and as a concrete example of the kind of
administrative data integration recommended in prior assessments of that architecture [1].
Collaboration on sharing data, validation against official sources, or integration into E-LMIS
reporting is welcomed.</p>
<h2>For jobseekers</h2>
<p>The Top Skills table and the tables of leading skill by sector in this report (Analysis section)
are a practical starting point for prioritising which skills to acquire or present, particularly
where a skill appears as both persistent and leading in its sector rather than a single spike.</p>
<h2>Scaling and collaboration</h2>
<p>The underlying pipeline is built to extend to further sources, further geographies within the Horn
of Africa, and partnership with institutional initiatives in labour market information or ones
funded by donors. Readers interested in collaboration, sharing data, or funding this work are
invited to make contact (see the cover page for details).</p>
"""
    return page(body)


def build_references():
    items = "".join(f"<li id=\"ref-{i + 1}\">{esc(r)}</li>" for i, r in enumerate(REFERENCES))
    body = f"""
<h1 class="page-title" id="references">References</h1>
<ol class="ref-list">{items}</ol>
<p class="page-foot">&copy; {esc(AUTHOR_NAME)}. Prepared for {esc(PROJECT_NAME)}. Reach the author at {esc(AUTHOR_CONTACT)}.</p>
"""
    return page(body)


def render_report_html(report_title, period_label, window_label, generated_at, insights,
                        window_data, this_url_name, other_report_link, dashboard_link):
    current = insights["current"]
    pages = [
        build_cover(report_title, window_label, period_label, generated_at, other_report_link, dashboard_link),
        build_toc(),
        build_executive_summary(window_label, period_label, current, insights),
        build_introduction(window_label, period_label),
        build_sources(insights),
        build_analysis(window_label, current, insights, window_data),
        build_limitations(insights),
        build_conclusion(window_label, current, insights),
        build_recommendations(),
        build_references(),
    ]
    return PAGE_HEAD.format(title=esc(report_title)) + "\n".join(pages) + PAGE_TAIL


# ---------------------------------------------------------------------------
# PDF export (via the Chromium browser Playwright already installs)
# ---------------------------------------------------------------------------

def render_pdf(html_path, pdf_path):
    from playwright.sync_api import sync_playwright

    html_abs = Path(html_path).resolve()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page_ = browser.new_page()
        page_.goto(f"file://{html_abs}")
        page_.pdf(path=str(pdf_path), format="A4", print_background=True,
                  margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"})
        browser.close()


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

    reports = [
        ("weekly", f"{PROJECT_NAME} Weekly Vacancy and Skill Report",
         f"Covers the 7 days up to {as_of_date}", "week", weekly_insights,
         rollups["windows"]["weekly"], "weekly-intelligence-report.html", "monthly-intelligence-report.html"),
        ("monthly", f"{PROJECT_NAME} Monthly Vacancy and Skill Report",
         f"Covers the 30 days up to {as_of_date}", "month", monthly_insights,
         rollups["windows"]["monthly"], "monthly-intelligence-report.html", "weekly-intelligence-report.html"),
    ]

    Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    for key, title, period_label, window_label, insights, window_data, this_name, other_name in reports:
        html = render_report_html(
            report_title=title, period_label=period_label, window_label=window_label,
            generated_at=rollups["generated_at"], insights=insights, window_data=window_data,
            this_url_name=this_name, other_report_link=other_name, dashboard_link="../index.html",
        )
        html_path = Path(REPORTS_DIR) / f"{key}-intelligence-report.html"
        html_path.write_text(html)
        pdf_path = Path(REPORTS_DIR) / f"{key}-intelligence-report.pdf"
        try:
            render_pdf(html_path, pdf_path)
        except Exception as exc:  # pragma: no cover - PDF export must never block the HTML report
            print(f"  Warning: could not render {pdf_path} ({exc}). HTML report was still written.")

    print(f"Wrote {REPORTS_DIR}/weekly-intelligence-report.{{html,pdf}} and "
          f"{REPORTS_DIR}/monthly-intelligence-report.{{html,pdf}} "
          f"(history now has {len(history)} daily snapshot(s)).")


if __name__ == "__main__":
    main()
