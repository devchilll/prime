"""HTML parsing helpers (stdlib only, no BeautifulSoup dependency).

The AAO listing page returns ordinary HTML containing anchor tags that link
to decision PDFs under ``/sites/default/files/err/``. We only need to pull
those hrefs out, so a small ``html.parser.HTMLParser`` subclass is enough and
avoids a third-party dependency.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import unquote, urljoin

from .config import BASE_URL, ERR_PREFIX, Category


class _DecisionLinkParser(HTMLParser):
    """Collects href values for anchors that point at decision PDFs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def _matches_category(path: str, category: Category) -> bool:
    """True if a decision PDF path belongs to *category*.

    Checks the filename code suffix (e.g. ``...B2203.pdf``) and, as a
    fallback, the category directory segment (e.g.
    ``B2 - Aliens with Extraordinary Ability``). URL-decoded before matching
    because USCIS serves these paths with %20-escaped spaces.
    """
    decoded = unquote(path)
    filename = decoded.rsplit("/", 1)[-1]
    if re.search(rf"{re.escape(category.file_code)}\.pdf$", filename, re.IGNORECASE):
        return True
    return category.dir_name.lower() in decoded.lower()


def extract_decision_links(
    html: str,
    base_url: str = BASE_URL,
    category: Optional[Category] = None,
) -> List[str]:
    """Return absolute URLs of decision PDFs found in *html*.

    Filters anchors down to those whose path contains the USCIS ``/err/``
    prefix and ends in ``.pdf``. When *category* is given, links are further
    restricted to that category's filename code / directory, so unrelated
    ``/err/`` PDFs on the page are not mislabeled. Order is preserved;
    duplicates (including query-string variants of the same path) removed.
    """
    parser = _DecisionLinkParser()
    parser.feed(html)

    seen = set()
    links: List[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        # Normalise to the bare path URL: dedup and return without query/fragment.
        path_only = absolute.split("?", 1)[0].split("#", 1)[0]
        if ERR_PREFIX not in path_only:
            continue
        if not path_only.lower().endswith(".pdf"):
            continue
        if category is not None and not _matches_category(path_only, category):
            continue
        if path_only in seen:
            continue
        seen.add(path_only)
        links.append(path_only)
    return links
