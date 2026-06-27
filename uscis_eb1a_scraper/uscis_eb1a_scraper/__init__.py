"""USCIS AAO EB-1A decision scraper.

Discover and download USCIS Administrative Appeals Office (AAO) non-precedent
decisions, filtered by category (EB-1A / NIW), month, and year.
"""

from .config import CATEGORIES, Category, get_category
from .models import Decision
from .scraper import AAOScraper, build_listing_url, iter_year_months

__all__ = [
    "AAOScraper",
    "Decision",
    "Category",
    "CATEGORIES",
    "get_category",
    "build_listing_url",
    "iter_year_months",
]

__version__ = "0.1.0"
