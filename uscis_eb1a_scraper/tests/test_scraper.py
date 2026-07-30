"""Offline unit tests for the USCIS AAO EB-1A scraper.

These do not touch the network: parsing and URL-building are exercised against
HTML fixtures that mirror the structure of the real USCIS listing page.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from uscis_eb1a_scraper.config import get_category
from uscis_eb1a_scraper.models import Decision
from uscis_eb1a_scraper.parser import extract_decision_links
from uscis_eb1a_scraper.scraper import AAOScraper, build_listing_url, iter_year_months

FIXTURES = Path(__file__).parent / "fixtures"


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


# --- P0 fixes: dedup, category filtering, pagination -------------------------


def test_extract_decision_links_dedupes_query_variants():
    """a.pdf and a.pdf?download=1 are the same decision -> one link, path-only."""
    html = (FIXTURES / "listing_2025_05_page0.html").read_text()
    links = extract_decision_links(html)
    assert sum("MAY272025_02B2203" in link for link in links) == 1
    assert not any("?" in link for link in links)


def test_extract_decision_links_category_filter():
    """The stray NIW (B5203) link must be dropped for the eb1a category."""
    html = (FIXTURES / "listing_2025_05_page0.html").read_text()
    eb1a = get_category("eb1a")
    links = extract_decision_links(html, category=eb1a)
    assert links, "expected EB-1A links in fixture"
    assert all("B2203.pdf" in link for link in links)
    assert not any("B5203" in link for link in links)
    # Without a category filter the NIW link passes through.
    assert any("B5203" in link for link in extract_decision_links(html))


class _FakePagingClient:
    """Serves the two fixture pages, then an empty page, tracking URLs fetched."""

    def __init__(self):
        self.urls = []
        self.pages = {
            0: (FIXTURES / "listing_2025_05_page0.html").read_text(),
            1: (FIXTURES / "listing_2025_05_page1.html").read_text(),
        }

    def get_text(self, url, **kwargs):
        self.urls.append(url)
        if "page=" in url:
            page = int(url.rsplit("page=", 1)[-1])
        else:
            page = 0
        return self.pages.get(page, "<html><body>no results</body></html>")

    def close(self):
        pass


def test_fetch_month_paginates_and_validates_category():
    client = _FakePagingClient()
    scraper = AAOScraper(client=client)
    decisions = scraper.fetch_month("eb1a", 2025, 5)
    # page0 has 10 unique eb1a links (12 rows - 1 NIW - 1 query-dupe), page1 has 3.
    assert len(decisions) == 13
    assert all(d.file_code == "B2203" for d in decisions)
    assert all(d.category_key == "eb1a" for d in decisions)
    # page1 is short (<10 links) so page2 is never requested.
    assert any("page=1" in u for u in client.urls)
    assert not any("page=2" in u for u in client.urls)


def test_build_listing_url_with_page():
    assert build_listing_url(19, 2025, 5, page=1).endswith("uri_1=19&m=5&y=2025&page=1")
    assert "page" not in build_listing_url(19, 2025, 5, page=0)
