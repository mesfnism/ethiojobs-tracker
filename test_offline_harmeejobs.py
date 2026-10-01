"""
Offline tests for harmeejobs_pipeline.py's feed parser, against a real
<item> captured live from https://harmeejobs.com/?feed=job_feed (the
"Senior Project Officer II - NMIS, DMLP & DOTS" posting, job_id 55795) —
unlike reporterjobs_pipeline.py and devnetjobs_pipeline.py, this one is a
genuine captured fixture, not a structural reconstruction, since the feed
itself was read directly in-browser. A second, synthetic minimal item
exercises the honest-gap paths (no skills chips, no deadline line, no
salary anywhere) that the real item doesn't happen to hit.
"""

import xml.etree.ElementTree as ET

from harmeejobs_pipeline import (
    structure_job,
    job_id_from_guid,
    extract_feed_skills,
    extract_how_to_apply,
    extract_description,
    classify_education,
    extract_experience,
    extract_required_number,
    extract_deadline,
    extract_salary_hint,
    strip_tags_to_text,
)


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


# A real <item>, trimmed to the parts the parser actually reads, captured
# live from the site's own job_feed.
REAL_ITEM_XML = """<item xmlns:content="http://purl.org/rss/1.0/modules/content/"
                          xmlns:job_listing="https://harmeejobs.com">
<title>Senior Project Officer II &#8211; NMIS, DMLP &#038; DOTS</title>
<link>https://harmeejobs.com/job/senior-project-officer-ii-nmis-dmlp-dots/</link>
<pubDate>Tue, 29 Sep 2026 08:45:25 +0000</pubDate>
<guid isPermaLink="false">https://harmeejobs.com/?post_type=job_listing&#038;p=55795</guid>
<content:encoded><![CDATA[<div class="job-detail-description">
<h2 class="job-detail-description-title">About the Job</h2>
<div class="job-detail-description-details">
<ul>
<li><strong>Employment: One year with the possibility of extension</strong></li>
<li><strong>Duty Station: Addis Ababa, Secondment to MOTRI with travel to regional project sites as required.</strong></li>
<li><strong>Required Number: Two</strong></li>
<li><strong>Application Deadline: October 3, 2026</strong></li>
</ul>
<p>BACKGROUND text about the agency goes here for several paragraphs.</p>
</div>
</div>
<div class="job-detail-description">
<div class="job-detail-description-title">About You</div>
<div class="job-detail-description-details">
<p><strong>REQUIRED QUALIFICATIONS, COMPETENCIES, AND EXPERIENCE</strong></p>
<ul>
<li><strong>Education</strong>: At least a master&#8217;s degree in computer science, Information Systems, Information Technology, Software Engineering, Project Management, Data Science or data analysis or other related fields.</li>
<li><strong>Experience</strong>: Minimum of 8 years of relevant professional experience in digital agriculture, market information systems, digital platforms, or ICT-enabled development initiatives, of which at least 2 years in project coordination or leadership roles.</li>
</ul>
</div>
</div>
<div class="job-detail-description">
<div class="job-detail-description-title">Requirement Skill</div>
<div class="job-detail-description-details">
<div>
<div></div>
<div id="switch-list-label-wifi" class="MuiListItemText-root mui-style-ojtn1y"><span class="MuiTypography-root MuiTypography-body1 MuiListItemText-primary mui-style-1b4jcyw">Communication &amp; Negotiation</span></div>
</div>
</div>
</div>
<div id="how-to-apply-section" class="job-detail-description">
<h2 class="job-detail-description-title">How To Apply</h2>
<div>
<p><strong>APPLICATION INSTRUCTIONS</strong></p>
<p>We invite all candidates meeting the required qualifications to submit (i) a cover letter and (ii) a CV (maximum 5 pages) to <a href="https://apply.ati.gov.et/" target="_blank" rel="noopener">https://apply.ati.gov.et</a>. It is mandatory to mention the position title in both the subject line of your cover letter and the Outlook email subject line.</p>
<p>Women are highly encouraged to apply</p>
</div>
</div>
]]></content:encoded>
<job_listing:location><![CDATA[Addis Ababa]]></job_listing:location>
<job_listing:job_type><![CDATA[Full Time]]></job_listing:job_type>
<job_listing:job_category><![CDATA[Information Technology]]></job_listing:job_category>
<job_listing:company><![CDATA[Ethiopian Agricultural Transformation Agency (ATA)]]></job_listing:company>
</item>"""

# A minimal synthetic item exercising the honest-gap paths the real item
# doesn't hit: no skill chips, no "Required Number"/deadline line, no
# salary mentioned anywhere, no job_listing:* fields at all.
SPARSE_ITEM_XML = """<item xmlns:content="http://purl.org/rss/1.0/modules/content/"
                            xmlns:job_listing="https://harmeejobs.com">
<title>General Clerk</title>
<link>https://harmeejobs.com/job/general-clerk/</link>
<pubDate>not a real date</pubDate>
<guid isPermaLink="false">https://harmeejobs.com/?post_type=job_listing&#038;p=99999</guid>
<content:encoded><![CDATA[<div class="job-detail-description">
<h2 class="job-detail-description-title">About the Job</h2>
<div class="job-detail-description-details">
<p>We are hiring a clerk to support the office.</p>
</div>
</div>
]]></content:encoded>
</item>"""


def main():
    all_ok = True

    # --- job_id_from_guid ------------------------------------------------
    all_ok &= check(
        "job_id_from_guid extracts numeric id",
        job_id_from_guid("https://harmeejobs.com/?post_type=job_listing&p=55795") == "55795",
    )
    all_ok &= check("job_id_from_guid handles None", job_id_from_guid(None) is None)
    all_ok &= check("job_id_from_guid handles no match", job_id_from_guid("not a guid") is None)

    # --- strip_tags_to_text ------------------------------------------------
    stripped = strip_tags_to_text("<p>Hello <strong>world</strong></p><p>Next</p>")
    all_ok &= check("strip_tags_to_text removes tags, keeps text", "Hello" in stripped and "world" in stripped and "<" not in stripped)

    # --- parse the real captured item --------------------------------------
    real_item = ET.fromstring(REAL_ITEM_XML)

    all_ok &= check(
        "extract_feed_skills finds the Requirement Skill chip",
        extract_feed_skills(real_item.find("{http://purl.org/rss/1.0/modules/content/}encoded").text)
        == ["Communication & Negotiation"],
    )

    how_to_apply = extract_how_to_apply(real_item.find("{http://purl.org/rss/1.0/modules/content/}encoded").text)
    all_ok &= check(
        "extract_how_to_apply captures instructions, drops the heading itself",
        how_to_apply is not None
        and "cover letter" in how_to_apply
        and not how_to_apply.lower().startswith("how to apply"),
    )

    description = extract_description(real_item.find("{http://purl.org/rss/1.0/modules/content/}encoded").text)
    all_ok &= check(
        "extract_description captures About-the-Job/About-You, excludes skill chip and apply text",
        description is not None
        and "Required Number" in description
        and "master" in description.lower()
        and "Communication & Negotiation" not in description
        and "cover letter" not in description,
    )

    all_ok &= check("classify_education finds Master's degree in description", classify_education(description) == "Master's degree")
    all_ok &= check(
        "extract_experience captures the experience line",
        extract_experience(description) is not None and "8 years" in extract_experience(description),
    )
    all_ok &= check("extract_required_number captures 'Two'", extract_required_number(description) == "Two")
    all_ok &= check("extract_deadline captures the date", extract_deadline(description) == "October 3, 2026")
    all_ok &= check("extract_salary_hint honestly None (no salary anywhere in this posting)", extract_salary_hint(description) is None)

    structured = structure_job(real_item)
    all_ok &= check("structured: job_id from guid", structured["job_id"] == "55795")
    all_ok &= check("structured: job_title decodes HTML entities", structured["job_title"] == "Senior Project Officer II – NMIS, DMLP & DOTS")
    all_ok &= check("structured: employer from job_listing:company", structured["employer"] == "Ethiopian Agricultural Transformation Agency (ATA)")
    all_ok &= check("structured: category from job_listing:job_category", structured["category"] == "Information Technology")
    all_ok &= check("structured: location from job_listing:location", structured["location"] == "Addis Ababa")
    all_ok &= check("structured: employment_type from job_listing:job_type", structured["employment_type"] == "Full Time")
    all_ok &= check("structured: skills_required joined", structured["skills_required"] == "Communication & Negotiation")
    all_ok &= check("structured: education_required classified", structured["education_required"] == "Master's degree")
    all_ok &= check("structured: application_deadline", structured["application_deadline"] == "October 3, 2026")
    all_ok &= check("structured: number_required", structured["number_required"] == "Two")
    all_ok &= check("structured: source label", structured["source"] == "HarmeeJobs")
    all_ok &= check("structured: source_url from link", structured["source_url"] == "https://harmeejobs.com/job/senior-project-officer-ii-nmis-dmlp-dots/")
    all_ok &= check("structured: extraction_method flagged", structured["extraction_method"] == "rss_feed")
    all_ok &= check(
        "structured: date_posted_relative holds a REAL parsed date (more precise than other sources)",
        structured["date_posted_relative"] == "2026-09-29",
    )
    all_ok &= check("structured: salary_hint honestly None", structured["salary_hint"] is None)

    # --- parse the sparse/synthetic item (honest-gap paths) -----------------
    sparse_item = ET.fromstring(SPARSE_ITEM_XML)
    sparse_structured = structure_job(sparse_item)
    all_ok &= check("sparse: job_id still resolves", sparse_structured["job_id"] == "99999")
    all_ok &= check("sparse: skills_required is None (no chips present)", sparse_structured["skills_required"] is None)
    all_ok &= check("sparse: employer is None (no job_listing:company element at all)", sparse_structured["employer"] is None)
    all_ok &= check("sparse: category is None", sparse_structured["category"] is None)
    all_ok &= check("sparse: number_required is None (no 'Required Number' line)", sparse_structured["number_required"] is None)
    all_ok &= check("sparse: application_deadline is None", sparse_structured["application_deadline"] is None)
    all_ok &= check("sparse: education_required is None, not guessed", sparse_structured["education_required"] is None)
    all_ok &= check("sparse: malformed pubDate doesn't crash, date_posted_relative is None", sparse_structured["date_posted_relative"] is None)
    all_ok &= check("sparse: how_to_apply is None (no how-to-apply-section marker)", sparse_structured["how_to_apply"] is None)

    # --- structure_job: missing guid entirely -> None (caller skips it) -----
    no_guid_item = ET.fromstring("<item><title>X</title></item>")
    all_ok &= check("structure_job returns None when no resolvable job_id", structure_job(no_guid_item) is None)

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
