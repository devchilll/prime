"""HTML parsing helpers (stdlib only, no BeautifulSoup dependency).

The AAO listing page returns ordinary HTML containing anchor tags that link
to decision PDFs under ``/sites/default/files/err/``. We only need to pull
those hrefs out, so a small ``html.parser.HTMLParser`` subclass is enough and
avoids a third-party dependency.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import List
from urllib.parse import urljoin

from .config import BASE_URL, ERR_PREFIX


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


def extract_decision_links(html: str, base_url: str = BASE_URL) -> List[str]:
    """Return absolute URLs of decision PDFs found in *html*.

    Filters anchors down to those whose path contains the USCIS ``/err/``
    prefix and ends in ``.pdf``. Order is preserved and duplicates removed.
    """
    parser = _DecisionLinkParser()
    parser.feed(html)

    seen = set()
    links: List[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        # Normalise: drop any query/fragment for de-duplication purposes.
        path_part = absolute.split("?", 1)[0].split("#", 1)[0]
        if ERR_PREFIX not in path_part:
            continue
        if not path_part.lower().endswith(".pdf"):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links
