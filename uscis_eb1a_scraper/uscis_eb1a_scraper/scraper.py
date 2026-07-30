"""Core scraping logic for USCIS AAO non-precedent decisions.

Typical usage::

    from uscis_eb1a_scraper.scraper import AAOScraper

    scraper = AAOScraper()
    # All EB-1A decisions published in May 2025:
    decisions = scraper.fetch_month("eb1a", year=2025, month=5)

    # Every EB-1A decision across a date range:
    decisions = scraper.fetch_range("eb1a", start=(2024, 1), end=(2025, 12))
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlencode

from .client import HttpClient
from .config import LISTING_URL, get_category
from .models import Decision
from .parser import extract_decision_links

logger = logging.getLogger(__name__)

YearMonth = Tuple[int, int]


def build_listing_url(uri_1: int, year: int, month: int, page: int = 0) -> str:
    """Build the AAO listing URL for a category / month / year (and page).

    >>> build_listing_url(19, 2025, 5)
    'https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions?uri_1=19&m=5&y=2025'
    """
    params = {"uri_1": uri_1, "m": month, "y": year}
    if page > 0:
        params["page"] = page  # Drupal-style zero-based pagination
    return f"{LISTING_URL}?{urlencode(params)}"


def iter_year_months(start: YearMonth, end: YearMonth) -> Iterable[YearMonth]:
    """Yield (year, month) tuples from *start* to *end* inclusive."""
    (sy, sm), (ey, em) = start, end
    if (sy, sm) > (ey, em):
        raise ValueError("start must not be after end")
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield (y, m)
        m += 1
        if m > 12:
            m = 1
            y += 1


class AAOScraper:
    """Scrapes published AAO non-precedent decisions by category and date."""

    def __init__(self, client: Optional[HttpClient] = None) -> None:
        self.client = client or HttpClient()

    #: Safety cap on listing pagination (no real month needs this many pages).
    MAX_PAGES = 50

    def fetch_month(self, category, year: int, month: int) -> List[Decision]:
        """Return all decisions for *category* published in (year, month).

        Follows Drupal-style ``page=N`` pagination until a page yields no new
        decision links (or ``MAX_PAGES`` is hit). *category* may be a key
        (``"eb1a"``), a uri_1 value (``19``), or a
        :class:`~uscis_eb1a_scraper.config.Category`.
        """
        cat = category if hasattr(category, "uri_1") else get_category(category)
        seen = set()
        decisions: List[Decision] = []

        for page in range(self.MAX_PAGES):
            url = build_listing_url(cat.uri_1, year, month, page=page)
            logger.info(
                "Fetching %s decisions for %04d-%02d page %d: %s",
                cat.key, year, month, page, url,
            )
            html = self.client.get_text(url)
            links = extract_decision_links(html, category=cat)
            new_links = [link for link in links if link not in seen]
            if not new_links:
                break
            seen.update(new_links)
            decisions.extend(
                Decision.from_url(
                    link, category_key=cat.key, listing_year=year, listing_month=month
                )
                for link in new_links
            )
            # A short page means we've reached the last one; don't fetch page+1.
            if len(links) < 10:
                break

        logger.info(
            "Found %d decision link(s) for %04d-%02d", len(decisions), year, month
        )
        return decisions

    def discover(self, category, start: YearMonth, end: YearMonth) -> List[Decision]:
        """Alias for :meth:`fetch_range` — discovery pass over a month range."""
        return self.fetch_range(category, start, end)

    def fetch_range(
        self, category, start: YearMonth, end: YearMonth
    ) -> List[Decision]:
        """Return de-duplicated decisions for *category* across a date range."""
        seen = set()
        out: List[Decision] = []
        for year, month in iter_year_months(start, end):
            for decision in self.fetch_month(category, year, month):
                if decision.url in seen:
                    continue
                seen.add(decision.url)
                out.append(decision)
        return out

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "AAOScraper":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
