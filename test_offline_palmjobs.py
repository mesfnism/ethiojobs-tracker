"""
Offline tests for palmjobs_pipeline.py's data-shaping functions, against
mock rows shaped exactly like the public Supabase `jobs` table API
response captured during live reconnaissance (field names, list-typed
required_skills, numeric min/max salary, etc.). This cannot exercise
capture_api_headers()/fetch_jobs_page() themselves — those need a live
network+browser, unavailable in this sandbox — so this test covers the
pure functions: normalize_education(), build_salary_hint(), and
structure_job(), which is where any schema-mapping mistake would show up
first and most cheaply.
"""

from palmjobs_pipeline import (
    normalize_education, build_salary_hint, structure_job, COLUMNS,
    _filter_identifying_headers, fetch_jobs_page,
)


class _FakeResponse:
    def __init__(self, status, json_data=None, text=""):
        self.status = status
        self._json_data = json_data
        self._text = text

    def json(self):
        return self._json_data

    def text(self):
        return self._text


class _FakeRequestContext:
    """Captures the headers it was called with, so the Accept-header
    override in fetch_jobs_page can be tested without a real Playwright
    APIRequestContext or network access."""

    def __init__(self, status=200, json_data=None):
        self.last_headers = None
        self._status = status
        self._json_data = json_data if json_data is not None else []

    def get(self, url, headers=None, timeout=None):
        self.last_headers = headers
        return _FakeResponse(self._status, json_data=self._json_data)


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition


def main():
    all_ok = True

    # --- normalize_education -------------------------------------------------
    all_ok &= check("education: PhD", normalize_education("PhD in Economics") == "PhD")
    all_ok &= check("education: Doctorate variant", normalize_education("Doctoral degree required") == "PhD")
    all_ok &= check("education: Master's", normalize_education("MA/MSc Master's Degree") == "Master's degree")
    all_ok &= check("education: Bachelor's", normalize_education("BA/BSc Bachelor's Degree") == "Bachelor's degree")
    all_ok &= check("education: undergraduate alias", normalize_education("Undergraduate degree") == "Bachelor's degree")
    all_ok &= check("education: Diploma", normalize_education("Diploma in Accounting") == "Diploma")
    all_ok &= check("education: Certificate/TVET", normalize_education("TVET Level III Certificate") == "Certificate")
    all_ok &= check("education: None input", normalize_education(None) is None)
    all_ok &= check("education: unclassifiable text left None", normalize_education("Relevant qualification") is None)

    # --- build_salary_hint -----------------------------------------------------
    all_ok &= check(
        "salary: range with currency",
        build_salary_hint({"currency": "ETB", "min_salary": 15000, "max_salary": 20000})
        == "ETB 15000 - 20000",
    )
    all_ok &= check(
        "salary: single amount (min only)",
        build_salary_hint({"currency": "ETB", "min_salary": 12000, "max_salary": None}) == "ETB 12000",
    )
    all_ok &= check(
        "salary: single amount (max only)",
        build_salary_hint({"currency": "USD", "min_salary": None, "max_salary": 800}) == "USD 800",
    )
    all_ok &= check(
        "salary: equal min/max collapses to one amount",
        build_salary_hint({"currency": "ETB", "min_salary": 10000, "max_salary": 10000}) == "ETB 10000",
    )
    all_ok &= check(
        "salary: both missing -> None",
        build_salary_hint({"currency": "ETB", "min_salary": None, "max_salary": None}) is None,
    )
    all_ok &= check(
        "salary: USD preserved as text (not silently converted)",
        build_salary_hint({"currency": "USD", "min_salary": 500, "max_salary": 700}) == "USD 500 - 700",
    )
    all_ok &= check(
        "salary: missing currency key doesn't crash",
        build_salary_hint({"min_salary": 5000, "max_salary": None}) == "5000",
    )

    # --- structure_job -----------------------------------------------------
    api_row_full = {
        "id": "9c3f2e1a-7b4d-4a1e-9c2f-5d6e7f8a9b0c",
        "employer_id": "emp-1",
        "company_id": "co-1",
        "company_name": "Zemen Bank S.C.",
        "job_title": "Senior Risk Officer",
        "job_location": "Addis Ababa",
        "open_positions": 2,
        "job_type": "Full-Time",
        "experience_level": "5+ years",
        "min_salary": 18000,
        "max_salary": 25000,
        "currency": "ETB",
        "job_description": "Looking for a Finance and Risk Management specialist.",
        "application_deadline": "2026-11-15",
        "external_link": "https://zemenbank.com/careers/apply/123",
        "job_status": "Active",
        "date_posted": "3 days ago",
        "created_at": "2026-09-28T10:00:00Z",
        "job_industry": "Banking and Finance",
        "education_level": "Master's Degree",
        "field_of_study": "Finance, Accounting, Economics",
        "required_skills": ["Risk Analysis", "Financial Modeling", "Compliance"],
        "is_aggregated": False,
        "source_url": None,
        "source_name": None,
        "email_application": None,
    }
    row = structure_job(api_row_full)

    all_ok &= check("structured: job_id is real UUID from API", row["job_id"] == api_row_full["id"])
    all_ok &= check("structured: job_title", row["job_title"] == "Senior Risk Officer")
    all_ok &= check("structured: employer", row["employer"] == "Zemen Bank S.C.")
    all_ok &= check("structured: category from job_industry", row["category"] == "Banking and Finance")
    all_ok &= check("structured: location", row["location"] == "Addis Ababa")
    all_ok &= check("structured: number_required", row["number_required"] == 2)
    all_ok &= check("structured: education normalized", row["education_required"] == "Master's degree")
    all_ok &= check("structured: years_experience_required passthrough", row["years_experience_required"] == "5+ years")
    all_ok &= check(
        "structured: skills_required joined from list",
        row["skills_required"] == "Risk Analysis; Financial Modeling; Compliance",
    )
    all_ok &= check("structured: salary_hint built", row["salary_hint"] == "ETB 18000 - 25000")
    all_ok &= check("structured: application_deadline", row["application_deadline"] == "2026-11-15")
    all_ok &= check("structured: how_to_apply from external_link", row["how_to_apply"] == api_row_full["external_link"])
    all_ok &= check("structured: source_url includes job id", api_row_full["id"] in row["source_url"])
    all_ok &= check("structured: source label", row["source"] == "PalmJobs")
    all_ok &= check("structured: date_posted_relative passthrough", row["date_posted_relative"] == "3 days ago")
    all_ok &= check("structured: extraction_method flagged as public_api", row["extraction_method"] == "public_api")
    all_ok &= check(
        "structured: description prefers job_description over field_of_study",
        row["description"] == "Looking for a Finance and Risk Management specialist.",
    )
    all_ok &= check(
        "structured: every COLUMNS key present (schema parity with other pipelines)",
        all(c in row for c in COLUMNS),
    )

    # --- structure_job: fallbacks and missing-data edge cases -----------------
    api_row_sparse = {
        "id": "a1b2c3d4-0000-0000-0000-000000000000",
        "company_name": "Unnamed NGO",
        "job_title": "Program Officer",
        "job_location": None,
        "open_positions": None,
        "job_type": None,
        "experience_level": None,
        "min_salary": None,
        "max_salary": None,
        "currency": None,
        "job_description": None,
        "application_deadline": None,
        "external_link": None,
        "job_status": "Active",
        "date_posted": None,
        "created_at": None,
        "job_industry": None,
        "education_level": None,
        "field_of_study": "Social Work",
        "required_skills": None,
        "is_aggregated": False,
        "source_url": None,
        "source_name": None,
        "email_application": "hr@example.org",
    }
    sparse_row = structure_job(api_row_sparse)
    all_ok &= check("sparse: salary_hint is None when no salary data", sparse_row["salary_hint"] is None)
    all_ok &= check("sparse: education_required is None, not guessed", sparse_row["education_required"] is None)
    all_ok &= check("sparse: skills_required is None (not list) -> falls back to None", sparse_row["skills_required"] is None)
    all_ok &= check(
        "sparse: description falls back to field_of_study when job_description missing",
        sparse_row["description"] == "Social Work",
    )
    all_ok &= check(
        "sparse: how_to_apply falls back to email_application when no external_link",
        sparse_row["how_to_apply"] == "hr@example.org",
    )

    # --- _filter_identifying_headers (the 2026-10-02 PGRST116 fix) --------
    full_captured = {
        "apikey": "anon-key-abc",
        "Authorization": "Bearer anon-key-abc",
        "Accept": "application/vnd.pgrst.object+json",  # the actual culprit header
        "Range": "0-0",
        "User-Agent": "Mozilla/5.0 ...",
        "Referer": "https://palmjobs.et/jobs",
    }
    filtered = _filter_identifying_headers(full_captured)
    all_ok &= check("header filter: keeps apikey", filtered.get("apikey") == "anon-key-abc")
    all_ok &= check("header filter: keeps Authorization", filtered.get("Authorization") == "Bearer anon-key-abc")
    all_ok &= check("header filter: drops the singular-object Accept header",
                     "Accept" not in filtered and "accept" not in filtered)
    all_ok &= check("header filter: drops Range/User-Agent/Referer", len(filtered) == 2)

    # Case-insensitive input (real headers dicts aren't guaranteed casing).
    lowercase_captured = {"apikey": "k", "authorization": "Bearer k", "accept": "application/vnd.pgrst.object+json"}
    filtered_lower = _filter_identifying_headers(lowercase_captured)
    all_ok &= check("header filter: case-insensitive apikey", filtered_lower.get("apikey") == "k")
    all_ok &= check("header filter: case-insensitive Authorization", filtered_lower.get("Authorization") == "Bearer k")
    all_ok &= check("header filter: still drops lowercase accept", len(filtered_lower) == 2)

    # Nothing identifying present -> empty dict, not a crash, not None.
    all_ok &= check("header filter: empty input -> {}", _filter_identifying_headers({}) == {})

    # --- fetch_jobs_page always sends its own Accept, even if the caller's
    # headers dict (however it was obtained) still carried a stray one ---
    poisoned_headers = {"apikey": "k", "Authorization": "Bearer k", "Accept": "application/vnd.pgrst.object+json"}
    fake_ctx = _FakeRequestContext(status=200, json_data=[{"id": "1"}])
    fetch_jobs_page(fake_ctx, "https://example.supabase.co/rest/v1/jobs", poisoned_headers, offset=0)
    all_ok &= check(
        "fetch_jobs_page overrides a poisoned Accept header with application/json",
        fake_ctx.last_headers.get("Accept") == "application/json",
    )
    all_ok &= check(
        "fetch_jobs_page still sends the real apikey/Authorization through",
        fake_ctx.last_headers.get("apikey") == "k" and fake_ctx.last_headers.get("Authorization") == "Bearer k",
    )

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
