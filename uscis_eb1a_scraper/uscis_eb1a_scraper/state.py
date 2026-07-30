"""Pipeline state: the JSONL manifest and the single-run pipeline lock.

The manifest (``data/manifest.jsonl``) is the pipeline's source of truth for
which AAO decisions have been discovered, downloaded, and extracted. One JSON
object per line, sorted deterministically so that re-serializing an unchanged
manifest produces a byte-identical file (git-friendly diffs).

Manifest line schema (frozen -- do not change key names):

    {
      "id":                "<filename minus .pdf>",
      "filename":          "MAY282025_05B2203.pdf",
      "url":               "<absolute PDF URL, query string stripped>",
      "category":          "eb1a",
      "decision_date":     "YYYY-MM-DD" | null,
      "listing_months":    ["YYYY-MM", ...],   # listing pages it appeared on
      "first_seen_at":     iso8601Z,
      "sha256":            str | null,
      "size_bytes":        int | null,
      "downloaded_at":     iso8601Z | null,
      "download_status":   "pending" | "ok" | "failed" | "not_pdf",
      "extraction_status": "pending" | "ok" | "empty_text" | "error",
      "extracted_at":      iso8601Z | null,
      "last_error":        str | null
    }

USCIS occasionally re-posts a decision under a trailing month's listing (they
backfill), so an entry keeps *every* listing month it was seen under in
``listing_months``; the first element is the month it was first discovered in.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from .models import Decision

logger = logging.getLogger(__name__)

DOWNLOAD_STATUSES = ("pending", "ok", "failed", "not_pdf")
EXTRACTION_STATUSES = ("pending", "ok", "empty_text", "error")

#: A pipeline lock older than this is considered abandoned and may be stolen.
LOCK_MAX_AGE = timedelta(hours=6)


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing ``Z``."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp produced by :func:`now_iso`."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (tmp file in same dir + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def entry_sort_key(entry: dict) -> tuple:
    """Sort by decision date (undated last), then filename, for stable output."""
    return (entry.get("decision_date") or "9999", entry.get("filename") or "")


class Manifest:
    """In-memory view of the manifest, keyed by decision filename.

    USCIS decision filenames (``MAY282025_05B2203.pdf``) are unique in
    practice. In the pathological case where the same filename shows up under
    two different URL paths, the second entry is keyed by its full URL so both
    are preserved (with a logged warning).
    """

    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}

    # -- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Load a manifest from *path*; a missing file yields an empty manifest."""
        manifest = cls()
        path = Path(path)
        if not path.exists():
            return manifest
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping bad manifest line %d: %s", lineno, exc)
                    continue
                key = entry["filename"]
                existing = manifest.entries.get(key)
                if existing is not None and existing["url"] != entry["url"]:
                    key = entry["url"]
                manifest.entries[key] = entry
        return manifest

    def save(self, path: Path) -> None:
        """Atomically write the manifest, one sorted JSON object per line."""
        lines = [
            json.dumps(entry, sort_keys=True, ensure_ascii=False)
            for entry in self.sorted_entries()
        ]
        atomic_write_text(Path(path), "\n".join(lines) + ("\n" if lines else ""))

    def sorted_entries(self) -> List[dict]:
        return sorted(self.entries.values(), key=entry_sort_key)

    def __len__(self) -> int:
        return len(self.entries)

    # -- discovery merge -------------------------------------------------

    def _find(self, filename: str, url: str) -> Optional[dict]:
        entry = self.entries.get(filename)
        if entry is not None and entry["url"] == url:
            return entry
        return self.entries.get(url)  # collision entry keyed by full URL

    def merge_discovered(
        self, decisions: Iterable[Decision], listing_month: str
    ) -> int:
        """Merge freshly scraped *decisions* found under *listing_month*.

        New decisions become pending entries; already-known decisions only
        gain *listing_month* in their ``listing_months`` (if absent). Returns
        the number of newly added entries.
        """
        new = 0
        for decision in decisions:
            existing = self._find(decision.filename, decision.url)
            if existing is not None:
                if listing_month not in existing["listing_months"]:
                    existing["listing_months"].append(listing_month)
                continue

            key = decision.filename
            if key in self.entries:
                logger.warning(
                    "Filename %s already known under URL %s; keeping new URL %s "
                    "as a separate entry",
                    decision.filename,
                    self.entries[key]["url"],
                    decision.url,
                )
                key = decision.url

            filename = decision.filename
            entry_id = filename[:-4] if filename.lower().endswith(".pdf") else filename
            self.entries[key] = {
                "id": entry_id,
                "filename": filename,
                "url": decision.url,
                "category": decision.category_key,
                "decision_date": (
                    decision.decision_date.isoformat()
                    if decision.decision_date
                    else None
                ),
                "listing_months": [listing_month],
                "first_seen_at": now_iso(),
                "sha256": None,
                "size_bytes": None,
                "downloaded_at": None,
                "download_status": "pending",
                "extraction_status": "pending",
                "extracted_at": None,
                "last_error": None,
            }
            new += 1
        return new

    # -- pending work ----------------------------------------------------

    def pending_downloads(self, retry_failed: bool = False) -> List[dict]:
        """Entries needing a download (optionally re-trying failed/not_pdf)."""
        wanted = {"pending"}
        if retry_failed:
            wanted |= {"failed", "not_pdf"}
        return [
            e for e in self.sorted_entries() if e["download_status"] in wanted
        ]

    def pending_extractions(self, force: bool = False) -> List[dict]:
        """Downloaded entries awaiting extraction (all downloaded when *force*)."""
        return [
            e
            for e in self.sorted_entries()
            if e["download_status"] == "ok"
            and (force or e["extraction_status"] == "pending")
        ]

    # -- reporting -------------------------------------------------------

    def summary(self) -> dict:
        """Counts by status plus the min/max listing months covered."""
        download_counts = {status: 0 for status in DOWNLOAD_STATUSES}
        extraction_counts = {status: 0 for status in EXTRACTION_STATUSES}
        months: set[str] = set()
        for entry in self.entries.values():
            download_counts[entry["download_status"]] = (
                download_counts.get(entry["download_status"], 0) + 1
            )
            extraction_counts[entry["extraction_status"]] = (
                extraction_counts.get(entry["extraction_status"], 0) + 1
            )
            months.update(entry.get("listing_months") or [])
        return {
            "total": len(self.entries),
            "download_status": download_counts,
            "extraction_status": extraction_counts,
            "months": {
                "min": min(months) if months else None,
                "max": max(months) if months else None,
                "count": len(months),
            },
        }


class PipelineLock:
    """Best-effort single-writer lock on ``<data_dir>/.lock``.

    Prevents two concurrent pipeline runs from clobbering the manifest.
    A lock file younger than :data:`LOCK_MAX_AGE` blocks (``RuntimeError``);
    an older one is presumed abandoned (e.g. a killed run) and is stolen with
    a warning. The lock is removed on exit, including on exceptions.
    """

    def __init__(self, data_dir: Path, max_age: timedelta = LOCK_MAX_AGE) -> None:
        self.lock_path = Path(data_dir) / ".lock"
        self.max_age = max_age

    def __enter__(self) -> "PipelineLock":
        if self.lock_path.exists():
            age: Optional[timedelta] = None
            info: dict = {}
            try:
                info = json.loads(self.lock_path.read_text(encoding="utf-8"))
                age = datetime.now(timezone.utc) - parse_iso(info["started_at"])
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("Unreadable lock file %s; stealing it", self.lock_path)
            if age is not None and age < self.max_age:
                raise RuntimeError(
                    f"Another pipeline run appears active (lock {self.lock_path}, "
                    f"pid {info.get('pid')}, started {info.get('started_at')}). "
                    f"Remove the lock file if that run is dead."
                )
            if age is not None:
                logger.warning(
                    "Stealing stale pipeline lock %s (age %s, pid %s)",
                    self.lock_path,
                    age,
                    info.get("pid"),
                )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
            json.dumps({"pid": os.getpid(), "started_at": now_iso()}),
            encoding="utf-8",
        )
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.lock_path.unlink()
        except FileNotFoundError:  # pragma: no cover - defensive
            pass
