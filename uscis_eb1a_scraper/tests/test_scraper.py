"""Offline unit tests for the USCIS AAO EB-1A scraper.

These do not touch the network: parsing and URL-building are exercised against
a small HTML fixture that mirrors the structure of the real USCIS listing page.
"""

from __future__ import annotations

from datetime import date

import pytest

from uscis_eb1a_scraper.config import get_category
from uscis_eb1a_scraper.models import Decision
from uscis_eb1a_scraper.parser import extract_decision_links
from uscis_eb1a_scraper.scraper import build_listing_url, iter_year_months


# A fragment shaped like the real listing: two EB-1A (B2203) decision links,
# an unrelated internal link, and an external link that must be ignored.
SAMPLE_HTML = """
<html><body>
  <table>
    <tr><td>
      <a href="/sites/default/files/err/B2 - Aliens with Extraordinary Ability/Decisions_Issued_in_2025/MAY282025_05B2203.pdf">May 28, 2025</a>
    </td></tr>
    <tr><td>
      <a href="https://www.uscis.gov/sites/default/files/err/B2 - Aliens with Extraordinary Ability/Decisions_Issued_in_2024/SEP302024_02B2203.pdf">Sep 30, 2024</a>
    </td></tr>
    <tr><td>
      <a href="/administrative-appeals/aao-decisions">Back to AAO decisions</a>
      <a href="https://example.com/other.pdf">external</a>
    </td></tr>
  </table>
</body></html>
"""


def test_build_listing_url_eb1a():
    url = build_listing_url(19, 2025, 5)
    assert url == (
        "https://www.uscis.gov/administrative-appeals/aao-decisions/"
        "aao-non-precedent-decisions?uri_1=19&m=5&y=2025"
    )


def test_get_category_resolves_key_and_uri():
    assert get_category("eb1a").uri_1 == 19
    assert get_category(19).key == "eb1a"
    assert get_category("19").key == "eb1a"
    assert get_category("niw").uri_1 == 18
    assert get_category(18).file_code == "B5203"


def test_get_category_rejects_unknown():
    with pytest.raises(ValueError):
        get_category("eb2c")


def test_extract_decision_links_filters_to_err_pdfs():
    links = extract_decision_links(SAMPLE_HTML)
    assert len(links) == 2
    assert all("/sites/default/files/err/" in link for link in links)
    assert all(link.lower().endswith(".pdf") for link in links)
    # Relative link should be made absolute.
    assert links[0].startswith("https://www.uscis.gov/")
    # External and non-err links are excluded.
    assert not any("example.com" in link for link in links)


def test_extract_decision_links_dedupes():
    doubled = SAMPLE_HTML + SAMPLE_HTML
    assert len(extract_decision_links(doubled)) == 2


def test_decision_from_url_parses_filename():
    url = (
        "https://www.uscis.gov/sites/default/files/err/"
        "B2 - Aliens with Extraordinary Ability/Decisions_Issued_in_2025/"
        "MAY282025_05B2203.pdf"
    )
    d = Decision.from_url(url, category_key="eb1a", listing_year=2025, listing_month=5)
    assert d.filename == "MAY282025_05B2203.pdf"
    assert d.decision_date == date(2025, 5, 28)
    assert d.sequence == 5
    assert d.file_code == "B2203"
    assert d.category_key == "eb1a"
    assert d.to_dict()["decision_date"] == "2025-05-28"


def test_decision_from_url_handles_unparseable_filename():
    d = Decision.from_url(
        "https://www.uscis.gov/sites/default/files/err/B2/weird-name.pdf",
        category_key="eb1a",
    )
    assert d.filename == "weird-name.pdf"
    assert d.decision_date is None
    assert d.sequence is None


def test_iter_year_months_inclusive_range():
    months = list(iter_year_months((2024, 11), (2025, 2)))
    assert months == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_iter_year_months_single_month():
    assert list(iter_year_months((2025, 5), (2025, 5))) == [(2025, 5)]


def test_iter_year_months_rejects_reversed_range():
    with pytest.raises(ValueError):
        list(iter_year_months((2025, 5), (2025, 4)))
