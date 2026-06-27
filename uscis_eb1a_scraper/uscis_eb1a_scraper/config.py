"""Configuration constants for the USCIS AAO EB-1A scraper.

The USCIS Administrative Appeals Office (AAO) publishes non-precedent
decisions at:

    https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions

The listing on that page is driven by a small set of query-string
parameters:

    uri_1   the case category (see ``CATEGORIES`` below)
    m       the month  (1-12)
    y       the year   (e.g. 2025)

For example, the following URL lists EB-1A (extraordinary ability)
decisions issued in May 2025::

    .../aao-non-precedent-decisions?uri_1=19&m=5&y=2025

Individual decisions are served as PDFs from a predictable path::

    /sites/default/files/err/<CATEGORY DIR>/Decisions_Issued_in_<YEAR>/<FILE>.pdf

e.g. ``.../err/B2 - Aliens with Extraordinary Ability/Decisions_Issued_in_2025/MAY282025_05B2203.pdf``
"""

from __future__ import annotations

from dataclasses import dataclass

# Host + the human-facing listing page that accepts the uri_1/m/y filters.
BASE_URL = "https://www.uscis.gov"
LISTING_PATH = "/administrative-appeals/aao-decisions/aao-non-precedent-decisions"
LISTING_URL = BASE_URL + LISTING_PATH

# Prefix under which every decision PDF lives.
ERR_PREFIX = "/sites/default/files/err/"

# USCIS returns HTTP 403 to clients that do not look like a browser, so we send
# a realistic desktop User-Agent. Override via the CLI / HttpClient if needed.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Be polite: minimum seconds between requests to the USCIS host.
DEFAULT_DELAY_SECONDS = 1.5
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 4


@dataclass(frozen=True)
class Category:
    """A USCIS AAO decision category.

    Attributes:
        uri_1: The numeric value used in the ``uri_1`` query parameter.
        key: Short machine-friendly identifier (e.g. ``"eb1a"``).
        name: Human-readable category name as used by USCIS.
        dir_name: The directory segment used in PDF URLs under ``/err/``.
        file_code: The trailing code that appears in decision filenames
            (e.g. ``"B2203"`` in ``MAY282025_05B2203.pdf``).
    """

    uri_1: int
    key: str
    name: str
    dir_name: str
    file_code: str


# The two employment-based categories the user cares about.
#
#   uri_1 = 19 -> EB-1A, "Aliens with Extraordinary Ability"      (file code B2203)
#   uri_1 = 18 -> NIW,   advanced degree / exceptional ability     (file code B5203)
#
# EB-1A is the project's primary target.
CATEGORIES: dict[str, Category] = {
    "eb1a": Category(
        uri_1=19,
        key="eb1a",
        name="Aliens with Extraordinary Ability",
        dir_name="B2 - Aliens with Extraordinary Ability",
        file_code="B2203",
    ),
    "niw": Category(
        uri_1=18,
        key="niw",
        name=(
            "Members of the Professions holding Advanced Degrees "
            "or Aliens of Exceptional Ability"
        ),
        dir_name=(
            "B5 - Members of the Professions holding Advanced Degrees "
            "or Aliens of Exceptional Ability"
        ),
        file_code="B5203",
    ),
}

# Reverse lookup by uri_1 value.
CATEGORIES_BY_URI: dict[int, Category] = {c.uri_1: c for c in CATEGORIES.values()}


def get_category(key_or_uri: "str | int") -> Category:
    """Resolve a category from a short key (``"eb1a"``) or a uri_1 value (``19``)."""
    if isinstance(key_or_uri, int):
        try:
            return CATEGORIES_BY_URI[key_or_uri]
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Unknown uri_1 category value: {key_or_uri}") from exc
    key = str(key_or_uri).strip().lower()
    if key in CATEGORIES:
        return CATEGORIES[key]
    if key.isdigit():
        return get_category(int(key))
    raise ValueError(
        f"Unknown category {key_or_uri!r}; expected one of "
        f"{sorted(CATEGORIES)} or a uri_1 value in {sorted(CATEGORIES_BY_URI)}"
    )
