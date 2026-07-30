"""Data model for a single AAO decision."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Optional

# Three-letter month abbreviations used in USCIS decision filenames.
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Matches filenames like "MAY282025_05B2203.pdf" or "SEP302024_02B2203.pdf":
#   <MON><DD><YYYY>_<seq><file_code>.pdf
_FILENAME_RE = re.compile(
    r"^(?P<mon>[A-Z]{3})(?P<day>\d{2})(?P<year>\d{4})_"
    r"(?P<seq>\d+)(?P<code>[A-Z]\d+)\.pdf$",
    re.IGNORECASE,
)


@dataclass
class Decision:
    """A single published AAO non-precedent decision.

    Attributes:
        url: Absolute URL to the decision PDF.
        filename: The PDF filename (e.g. ``MAY282025_05B2203.pdf``).
        category_key: Short category key this decision was found under (e.g. ``"eb1a"``).
        decision_date: Parsed decision date, if it could be derived from the filename.
        sequence: The per-day sequence number from the filename, if present.
        file_code: The trailing category code from the filename (e.g. ``"B2203"``).
        listing_year: The year filter (``y``) the listing was queried with.
        listing_month: The month filter (``m``) the listing was queried with.
    """

    url: str
    filename: str
    category_key: str
    decision_date: Optional[date] = None
    sequence: Optional[int] = None
    file_code: Optional[str] = None
    listing_year: Optional[int] = None
    listing_month: Optional[int] = None

    @classmethod
    def from_url(
        cls,
        url: str,
        category_key: str,
        listing_year: Optional[int] = None,
        listing_month: Optional[int] = None,
    ) -> "Decision":
        """Build a Decision from a PDF URL, parsing what we can from the filename."""
        filename = url.rsplit("/", 1)[-1]
        decision_date: Optional[date] = None
        sequence: Optional[int] = None
        file_code: Optional[str] = None

        match = _FILENAME_RE.match(filename)
        if match:
            mon = _MONTHS.get(match.group("mon").upper())
            if mon:
                try:
                    decision_date = date(
                        int(match.group("year")), mon, int(match.group("day"))
                    )
                except ValueError:
                    decision_date = None
            sequence = int(match.group("seq"))
            file_code = match.group("code").upper()

        return cls(
            url=url,
            filename=filename,
            category_key=category_key,
            decision_date=decision_date,
            sequence=sequence,
            file_code=file_code,
            listing_year=listing_year,
            listing_month=listing_month,
        )

    def to_dict(self) -> dict:
        """JSON-serializable dict (dates rendered as ISO strings)."""
        data = asdict(self)
        if self.decision_date is not None:
            data["decision_date"] = self.decision_date.isoformat()
        return data
