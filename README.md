# EthioJobs Pipeline — Phase 1 (private-sector slice)

Renders EthioJobs' job listings (they load via client-side JavaScript, so a
plain HTTP fetch returns an empty page — this needed a real browser), reads
off structured fields, and appends genuinely new postings into a running
Excel workbook (`ethiojobs_tracker.xlsx`).

This is the companion to the **ReliefWeb pipeline** (Phase 0), which covers
the donor/NGO/UN-system slice of the labour market. EthioJobs is Ethiopia's
largest general job board — private companies, banks, manufacturers,
schools, and some NGOs — so together the two give a much fuller picture of
the market than either alone.

## What it does, concretely

- Loads `ethiojobs.net/jobs`, page by page, sorted newest-first (the site's
  own default sort).
- Reads off each listing card: title, employer, category/sector tag(s),
  location, work type (Office/Hybrid/Remote), and deadline.
- For every **genuinely new** posting only, opens its detail page and reads
  off: career level, employment type, number of positions, education
  required, years of experience required, required skills, any
  special skill/training note, and how to apply — plus a best-effort salary
  read when the posting happens to mention one in free text (EthioJobs
  postings state pay far more often than ReliefWeb's do, though still not
  always).
- **Stops paginating once it hits 15 postings in a row it's already seen** —
  since the site sorts newest-first, that means it's caught up, so a daily
  run only touches what's actually new instead of re-walking the whole
  board every time.
- **Never re-processes a posting it's already seen**, the same as the
  ReliefWeb pipeline — dedup is by job ID, read back from the existing
  workbook.
- Logs every run to a `Run Log` tab (timestamp, new rows, running total).

## Why this one needs a browser (and the ReliefWeb one doesn't)

EthioJobs is a Next.js site that renders job data client-side; the initial
HTML response is a loading shell with no job content in it (verified before
building this — a raw fetch gets you a spinner, nothing else). This script
uses Playwright to actually load the page the way a browser would, wait for
the listings to render, then read the finished DOM. That's real overhead
compared to a one-line API call, which is exactly why this was scoped as a
separate follow-on rather than folded into the ReliefWeb script.

## One-time setup (about 10 minutes)

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```
2. That's it — no API key, no registration, no appname. EthioJobs' listings
   are public and the site's `robots.txt` explicitly welcomes AI crawlers by
   name (ClaudeBot, GPTBot, PerplexityBot are each listed as allowed), so
   this doesn't sit in the same "wait for approval" queue as ReliefWeb.

## Running it

```bash
python ethiojobs_pipeline.py
```

First run creates `ethiojobs_tracker.xlsx` next to the script and walks
through as many pages as it takes to reach the end of the (currently
~1,000-posting) live listing — expect the first run to take a while, since
every posting is new. Every run after that only touches new postings and
finishes in well under a minute for a typical day's volume.

**Run this on a machine with normal internet access** — your laptop, a
small VPS, or GitHub Actions (recommended — see below). It will not run
inside a network-sandboxed environment; Playwright needs to actually reach
ethiojobs.net.

## Running it automatically, every day, with zero manual triggering

A ready-made GitHub Actions workflow is included at
`.github/workflows/daily_ethiojobs_pipeline.yml`. To use it:

1. Push this folder into a (private is fine) GitHub repository — the same
   one as the ReliefWeb pipeline is a natural fit, since both write into
   the repo on their own daily schedule (offset by an hour so they don't
   collide).
2. Nothing else to configure — no secrets needed for this one.
3. It runs daily at 06:00 UTC automatically, installs Playwright's Chromium
   fresh each run, commits the updated spreadsheet back into the repo, and
   you can also trigger it manually from the Actions tab.

## Turning this into weekly / monthly / quarterly rollups

Same principle as the ReliefWeb pipeline: don't build separate pipelines
per cadence. This daily pipeline is the only thing that needs to run often;
rollups are a pivot/query over the `Jobs` tab, filtered by `date_scraped`
and grouped by `category` / `location`. If you want, once both pipelines
have a few weeks of real data, a short follow-on script can merge the two
`Jobs` tabs (ReliefWeb + EthioJobs) into one combined labour-market view —
worth doing once you can see what the combined data actually looks like.

## Known limitations (honest, not hidden)

- **Card categories can be noisy.** Some listings tag themselves with up to
  six category/sector labels at once (banks in particular do this); the
  `category` column joins all of them with `; ` rather than picking one, so
  filtering by sector may need a "contains" match rather than an exact one.
- **Salary is still inconsistent.** Better than ReliefWeb, but most postings
  still don't state pay — `salary_hint` is a best-effort free-text catch,
  not a guaranteed field.
- **Site markup can change.** This parses the page's rendered text by
  structural position and label text (e.g. `"Career Level :"`), not by
  fragile CSS class names where avoidable — but if EthioJobs redesigns the
  page, the parsing functions in `ethiojobs_pipeline.py`
  (`parse_card_text`, `parse_detail_text`) are the first place to check and
  update. `test_offline.py` is built from real captured page text
  specifically so a redesign shows up as a clear, fast-failing test rather
  than silent bad data.
- **Devex is still not covered.** Same JS-rendering issue as EthioJobs, but
  a different site structure — a genuinely separate script, not built here.

## Editing the taxonomy

Unlike the ReliefWeb pipeline, this one doesn't do offline keyword
extraction for skills — EthioJobs postings already list required skills as
a structured bullet list on the detail page (`Requirement Skill`), so it's
read directly rather than inferred. If that changes (e.g. EthioJobs drops
the structured list for some postings), the fallback logic would live in
`parse_detail_text()`.

## Testing changes safely

`test_offline.py` runs the full parsing → structuring → dedup → Excel-write
logic against **real text captured from the live site**, with no
network/browser call at all. Run it after any change to
`ethiojobs_pipeline.py` before trusting it against the live site:

```bash
python test_offline.py
```
