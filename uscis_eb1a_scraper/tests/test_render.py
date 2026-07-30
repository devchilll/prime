"""Tests for render_dashboard: token filling, safety, and self-containment."""
import copy
import json
import re
from pathlib import Path

import pytest

from uscis_eb1a_scraper.analytics import compute_analytics
from uscis_eb1a_scraper.render import render_dashboard

FIXTURE = Path(__file__).parent / "fixtures" / "cases_sample.jsonl"
TEMPLATE = (
    Path(__file__).parent.parent / "uscis_eb1a_scraper" / "templates" / "dashboard.html"
)
GENERATED_AT = "2026-07-30T12:00:00Z"


@pytest.fixture(scope="module")
def cases():
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def analytics(cases):
    return compute_analytics(cases, GENERATED_AT)


@pytest.fixture()
def rendered(cases, analytics, tmp_path):
    out = tmp_path / "site" / "index.html"  # parent does not exist yet
    render_dashboard(analytics, cases, TEMPLATE, out)
    return out


def _extract_json_block(html: str, element_id: str) -> str:
    match = re.search(
        r'<script type="application/json" id="%s">(.*?)</script>' % element_id,
        html,
        re.DOTALL,
    )
    assert match, f"missing JSON block {element_id}"
    return match.group(1)


def test_output_written_atomically_with_parents(rendered, tmp_path):
    assert rendered.exists()
    # No leftover temp files from the atomic write.
    assert [p.name for p in rendered.parent.iterdir()] == ["index.html"]


def test_json_blocks_parse(rendered, analytics):
    html = rendered.read_text(encoding="utf-8")
    assert "/*__ANALYTICS_JSON__*/" not in html
    assert "/*__CASES_JSON__*/" not in html
    parsed_analytics = json.loads(_extract_json_block(html, "analytics-data"))
    assert parsed_analytics == analytics
    parsed_cases = json.loads(_extract_json_block(html, "cases-data"))
    assert len(parsed_cases) == 12
    for row in parsed_cases:
        assert set(row) == {"id", "d", "o", "t", "m", "n", "u"}


def test_compact_cases_sorted_date_desc(rendered):
    html = rendered.read_text(encoding="utf-8")
    parsed_cases = json.loads(_extract_json_block(html, "cases-data"))
    dates = [row["d"] for row in parsed_cases if row["d"] is not None]
    assert dates == sorted(dates, reverse=True)
    assert parsed_cases[0]["d"] == "2025-06-30"
    assert parsed_cases[-1]["d"] == "2024-01-10"


def test_compact_case_fields(rendered):
    html = rendered.read_text(encoding="utf-8")
    parsed_cases = json.loads(_extract_json_block(html, "cases-data"))
    sustained = next(c for c in parsed_cases if c["o"] == "sustained")
    assert sustained["id"] == "JUN182024_01B2203"
    assert sustained["t"] == "appeal"
    assert sustained["n"] == 4
    # Met-criteria keys in canonical order.
    assert sustained["m"] == [
        "awards", "published_material", "artistic_exhibitions", "leading_critical_role",
    ]
    assert sustained["u"].startswith("https://www.uscis.gov/")


def test_no_external_requests(rendered):
    """Only uscis.gov case links and the w3.org SVG namespace may appear."""
    html = rendered.read_text(encoding="utf-8")
    allowed = ("https://www.uscis.gov/", "http://www.w3.org/2000/svg")
    for match in re.finditer(r"http", html):
        snippet = html[match.start(): match.start() + 80]
        assert snippet.startswith(allowed), f"unexpected external reference: {snippet[:60]!r}"


def test_not_legal_advice_present(rendered):
    html = rendered.read_text(encoding="utf-8").lower()
    assert "not legal advice" in html


def test_script_close_escaping(analytics, cases, tmp_path):
    """A '</' sequence in the data must be escaped as '<\\/' in the output."""
    evil_cases = copy.deepcopy(cases)
    evil_cases[0]["id"] = "EVIL</script><script>x()"
    evil_analytics = compute_analytics(evil_cases, GENERATED_AT)
    out = tmp_path / "evil.html"
    render_dashboard(evil_analytics, evil_cases, TEMPLATE, out)
    html = out.read_text(encoding="utf-8")
    cases_json = _extract_json_block(html, "cases-data")
    # The raw close-tag never survives inside the JSON block...
    assert "</script>" not in cases_json
    assert "<\\/" in cases_json
    # ...and the escaped JSON still round-trips to the original string.
    parsed = json.loads(cases_json)
    assert any(row["id"] == "EVIL</script><script>x()" for row in parsed)


def test_missing_token_raises(analytics, cases, tmp_path):
    bad_template = tmp_path / "bad.html"
    bad_template.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError):
        render_dashboard(analytics, cases, bad_template, tmp_path / "out.html")
