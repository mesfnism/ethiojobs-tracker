"""
Offline tests for palmjobs_pipeline.py's data-shaping functions, against
mock rows shaped exactly like the public Supabase `jobs` table API
response captured during live reconnaissance (field names, list-typed
required_skills, numeric min/max salary, etc.). This cannot exercise
discover_api_credentials()/fetch_jobs_page() against the real network —
unavailable in this sandbox — so this test covers the pure functions
(normalize_education(), build_salary_hint(), structure_job()) plus
fetch_jobs_page() and _chunk_script_urls() against fake curl_cffi-shaped
responses, which is where any schema-mapping or header mistake would show
up first and most cheaply.
"""

import palmjobs_pipeline
from palmjobs_pipeline import (
    normalize_education, build_salary_hint, structure_job, COLUMNS,
    fetch_jobs_page, _chunk_script_urls, discover_api_credentials,
    _get_with_retry,
)


class _FakeResponse:
    """Mimics curl_cffi's Response shape: status_code (not .status) and
    .text as a plain attribute/property (not a method call like
    Playwright's APIResponse.text()). `headers` defaults to an empty dict
    (no Retry-After), same as a plain 429 with no explicit retry hint."""

    def __init__(self, status_code, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers if headers is not None else {}

    def json(self):
        return self._json_data


class _FakeSession:
    """Captures the last headers/url it was called with and returns
    responses from a queue, so fetch_jobs_page's Accept-header override
    and discover_api_credentials' chunk-fetching loop can both be tested
    without a real curl_cffi session or network access."""

    def __init__(self, responses_by_url=None, default_status=200, default_json=None):
        self.last_headers = None
        self.last_url = None
        self.calls = []
        self._responses_by_url = responses_by_url or {}
        self._default_status = default_status
        self._default_json = default_json if default_json is not None else []

    def get(self, url, headers=None, timeout=None):
        self.last_headers = headers
        self.last_url = url
        self.calls.append(url)
        if url in self._responses_by_url:
            return self._responses_by_url[url]
        return _FakeResponse(self._default_status, json_data=self._default_json)


class _FakeFlakySession:
    """Returns 429 for the first `fail_times` calls to a given URL, then a
    real response — used to test _get_with_retry's retry-then-succeed
    path without a real network or a real site that rate-limits."""

    def __init__(self, fail_times, success_response, retry_after=None):
        self.fail_times = fail_times
        self.success_response = success_response
        self.retry_after = retry_after
        self.call_count = 0

    def get(self, url, headers=None, timeout=None):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            retry_headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return _FakeResponse(429, headers=retry_headers)
        return self.success_response


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

    # --- fetch_jobs_page always sends its own Accept, even if the caller's
    # headers dict (however it was obtained) still carried a stray one
    # (the 2026-10-02 PGRST116 fix: a reused singular-object Accept header
    # once broke pagination with "Cannot coerce the result to a single
    # JSON object") ---
    poisoned_headers = {"apikey": "k", "Authorization": "Bearer k", "Accept": "application/vnd.pgrst.object+json"}
    fake_session = _FakeSession(default_status=200, default_json=[{"id": "1"}])
    fetch_jobs_page(fake_session, "https://example.supabase.co/rest/v1/jobs", poisoned_headers, offset=0)
    all_ok &= check(
        "fetch_jobs_page overrides a poisoned Accept header with application/json",
        fake_session.last_headers.get("Accept") == "application/json",
    )
    all_ok &= check(
        "fetch_jobs_page still sends the real apikey/Authorization through",
        fake_session.last_headers.get("apikey") == "k" and fake_session.last_headers.get("Authorization") == "Bearer k",
    )

    # fetch_jobs_page raises (rather than returning something falsy) on a
    # non-200 status, so main()'s offset==0 hard-fail path actually fires.
    error_session = _FakeSession(default_status=403, default_json=None)
    error_session._responses_by_url = {}
    error_session_resp_raised = False
    try:
        fetch_jobs_page(error_session, "https://example.supabase.co/rest/v1/jobs", {"apikey": "k"}, offset=0)
    except RuntimeError:
        error_session_resp_raised = True
    all_ok &= check("fetch_jobs_page raises RuntimeError on non-200 status", error_session_resp_raised)

    # --- _chunk_script_urls (2026-10-02 part 2: curl_cffi rewrite) --------
    sample_html = """
    <html><head>
    <script src="/_next/static/chunks/app/jobs-abc123.js"></script>
    <script src="https://palmjobs.et/_next/static/chunks/framework-def456.js"></script>
    <script src="/_next/static/css/main-xyz.css"></script>
    <script src="/some/other/script.js"></script>
    </head></html>
    """
    chunk_urls = _chunk_script_urls(sample_html, "https://palmjobs.et/jobs")
    all_ok &= check(
        "chunk urls: finds both /_next/static/chunks/ script tags",
        len(chunk_urls) == 2,
    )
    all_ok &= check(
        "chunk urls: resolves a relative src to an absolute palmjobs.et URL",
        "https://palmjobs.et/_next/static/chunks/app/jobs-abc123.js" in chunk_urls,
    )
    all_ok &= check(
        "chunk urls: keeps an already-absolute src as-is",
        "https://palmjobs.et/_next/static/chunks/framework-def456.js" in chunk_urls,
    )
    all_ok &= check(
        "chunk urls: ignores non-chunk scripts (css, unrelated js)",
        all("chunks" in u for u in chunk_urls),
    )

    # --- discover_api_credentials (2026-10-02 part 2: curl_cffi rewrite) --
    # End-to-end: fetch the /jobs page -> find chunk URLs -> fetch each
    # chunk -> find the anon key + supabase URL patterns inside one of them.
    fake_anon_key = "eyJ" + ("a" * 80)  # matches _ANON_KEY_PATTERN's length requirement
    chunk_js_with_key = f'var k="{fake_anon_key}";var u="https://abcxyz123.supabase.co";'
    list_page_url = "https://palmjobs.et/jobs"
    chunk_url = "https://palmjobs.et/_next/static/chunks/app/jobs-abc123.js"
    discover_session = _FakeSession(responses_by_url={
        list_page_url: _FakeResponse(200, text=(
            '<html><head><script src="/_next/static/chunks/app/jobs-abc123.js">'
            '</script></head></html>'
        )),
        chunk_url: _FakeResponse(200, text=chunk_js_with_key),
    })
    found_headers, found_base_url = discover_api_credentials(discover_session)
    all_ok &= check(
        "discover_api_credentials: finds the anon key inside the chunk",
        found_headers is not None and found_headers.get("apikey") == fake_anon_key,
    )
    all_ok &= check(
        "discover_api_credentials: builds Authorization from the same key",
        found_headers is not None and found_headers.get("Authorization") == f"Bearer {fake_anon_key}",
    )
    all_ok &= check(
        "discover_api_credentials: builds the /rest/v1/jobs base URL from the discovered supabase URL",
        found_base_url == "https://abcxyz123.supabase.co/rest/v1/jobs",
    )

    # Credentials genuinely absent (e.g. site changed, or blocked) -> a
    # clean (None, None), not a crash, so main() can sys.exit(1) on it.
    no_creds_session = _FakeSession(responses_by_url={
        list_page_url: _FakeResponse(200, text="<html><head></head></html>"),
    })
    missing_headers, missing_base_url = discover_api_credentials(no_creds_session)
    all_ok &= check(
        "discover_api_credentials: no chunk scripts at all -> (None, None), not a crash",
        missing_headers is None and missing_base_url is None,
    )

    # --- _get_with_retry (2026-10-02 part 3: 429 != 403) --------------
    # Patch time.sleep for this block only, so the retry backoff doesn't
    # actually slow the test suite down.
    real_sleep = palmjobs_pipeline.time.sleep
    sleep_calls = []
    palmjobs_pipeline.time.sleep = lambda s: sleep_calls.append(s)
    try:
        success = _FakeResponse(200, json_data=[{"id": "1"}])
        flaky = _FakeFlakySession(fail_times=2, success_response=success)
        result = _get_with_retry(flaky, "https://palmjobs.et/jobs")
        all_ok &= check(
            "retry: eventually returns the real 200 response after two 429s",
            result.status_code == 200,
        )
        all_ok &= check("retry: retried exactly twice before succeeding", flaky.call_count == 3)
        all_ok &= check("retry: slept between each retry", len(sleep_calls) == 2)

        # Honors an explicit Retry-After header rather than the default backoff.
        sleep_calls.clear()
        flaky_with_hint = _FakeFlakySession(fail_times=1, success_response=success, retry_after="3")
        _get_with_retry(flaky_with_hint, "https://palmjobs.et/jobs")
        all_ok &= check("retry: honors a Retry-After header", sleep_calls == [3.0])

        # Gives up after exhausting retries and returns the last 429 as-is
        # (not a crash), so main()'s existing "no credentials found"
        # handling still applies.
        sleep_calls.clear()
        always_429 = _FakeFlakySession(fail_times=999, success_response=success)
        exhausted = _get_with_retry(always_429, "https://palmjobs.et/jobs")
        all_ok &= check("retry: gives up and returns the 429 after max attempts", exhausted.status_code == 429)
        all_ok &= check(
            "retry: never retries more than RETRY_429_MAX_ATTEMPTS times",
            always_429.call_count == palmjobs_pipeline.RETRY_429_MAX_ATTEMPTS + 1,
        )
    finally:
        palmjobs_pipeline.time.sleep = real_sleep

    print()
    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
