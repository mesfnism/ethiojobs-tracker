"""
Offline tests for kebenajobs_pipeline.py's feed parser. The fixtures
below are built from real feed items captured live on 2026-10-01 against
kebenajobs.com/?feed=job_feed (trimmed for length, but using the site's
real tag structure, CDATA wrapping and content shape).
"""

from kebenajobs_pipeline import (
    parse_feed_items,
    structure_item,
    is_noise_item,
    job_id_from_link,
    extract_deadline,
    html_to_text,
)

FEED_PAGE_1 = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">
<channel>
<title>Elelanajobs.com</title>
<item>
<title>Enat Bank SC</title>
<link>https://kebenajobs.com/job/enat-bank-sc-sept-30-26/</link>
<pubDate>Thu, 01 Oct 2026 07:31:56 +0000</pubDate>
<guid isPermaLink="false">https://kebenajobs.com/?post_type=job_listing&#038;p=46933</guid>
<description><![CDATA[Enat Bank SC External Vacancy Announcement...]]></description>
<content:encoded><![CDATA[<p><strong>Enat Bank SC External Vacancy Announcement</strong></p>
<p><strong>1. Attorney I</strong></p>
<p>Education: LL.B. Degree in Law.<br />
Work Experience: 2 years.</p>
<p><strong>How to Apply</strong><br />
Submit application in person.<br />
Application Deadline: October 02, 2026</p>
]]></content:encoded>
<job_listing:location><![CDATA[Ethiopia]]></job_listing:location>
</item>
<item>
<title>Ethiopia Airlines &#8211; Call For Language Proficency Assessment</title>
<link>https://kebenajobs.com/job/ethiopia-airlines-call-for-language-proficency-assessment-sept-30-26/</link>
<pubDate>Wed, 30 Sep 2026 20:06:45 +0000</pubDate>
<guid isPermaLink="false">https://kebenajobs.com/?post_type=job_listing&#038;p=46922</guid>
<description><![CDATA[Result Announcements...]]></description>
<content:encoded><![CDATA[<div><h1>Result Announcements</h1></div>]]></content:encoded>
<job_listing:location><![CDATA[Ethiopia]]></job_listing:location>
</item>
</channel>
</rss>"""

FEED_EMPTY_PAGE = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">
<channel>
<title>Elelanajobs.com</title>
</channel>
</rss>"""


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    items = parse_feed_items(FEED_PAGE_1)
    all_ok &= check("parses both items from a feed page", len(items) == 2)
    all_ok &= check("first item title", items[0]["title"] == "Enat Bank SC")
    all_ok &= check("first item link", items[0]["link"] == "https://kebenajobs.com/job/enat-bank-sc-sept-30-26/")
    all_ok &= check("first item location", items[0]["location"] == "Ethiopia")
    all_ok &= check("entities decoded in noise item title",
                     items[1]["title"] == "Ethiopia Airlines – Call For Language Proficency Assessment")

    all_ok &= check("empty page yields no items", parse_feed_items(FEED_EMPTY_PAGE) == [])

    all_ok &= check("vacancy item is not flagged as noise", not is_noise_item(items[0]["title"]))
    all_ok &= check("result-announcement item is flagged as noise", is_noise_item(items[1]["title"]))

    all_ok &= check(
        "job_id from link",
        job_id_from_link("https://kebenajobs.com/job/enat-bank-sc-sept-30-26/") == "enat-bank-sc-sept-30-26",
    )

    desc = html_to_text(items[0]["content_html"])
    all_ok &= check("html_to_text strips tags", "<p>" not in desc and "<strong>" not in desc)
    all_ok &= check("html_to_text keeps the text", "Attorney I" in desc)

    all_ok &= check("extract_deadline finds the deadline line",
                     extract_deadline(desc) is not None and "October 02, 2026" in extract_deadline(desc))
    all_ok &= check("extract_deadline returns None when absent", extract_deadline("no deadline info here") is None)

    row = structure_item(items[0])
    all_ok &= check("structured: job_id", row["job_id"] == "enat-bank-sc-sept-30-26")
    all_ok &= check("structured: title", row["job_title"] == "Enat Bank SC")
    all_ok &= check("structured: employer mirrors title (no separate field in the feed)",
                     row["employer"] == row["job_title"])
    all_ok &= check("structured: source label", row["source"] == "KebenaJobs")
    all_ok &= check("structured: extraction_method", row["extraction_method"] == "rss_feed")
    all_ok &= check("structured: deadline carried through", row["application_deadline"] is not None)
    all_ok &= check("structured: location defaults sensibly", row["location"] == "Ethiopia")

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
