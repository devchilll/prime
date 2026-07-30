"""Resumable, validated downloading of AAO decision PDFs.

USCIS serves decision PDFs from stable paths under ``/sites/default/files/err/``,
but a request can also come back as an HTML error page (login walls, WAF
blocks, deleted files) with a 200 status. Every download is therefore streamed
to a ``.partial-*`` file first and only promoted to its final name once the
content is confirmed to start with the ``%PDF-`` magic bytes, so the ``pdfs/``
tree never contains half-written or non-PDF files.

Layout (all under the data dir):

    pdfs/<YYYY>/<FILENAME>.pdf     # YYYY from the decision date, else "unknown"
    text/<YYYY>/<ID>.txt           # written later by the extraction step
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from .client import HttpClient
from .state import Manifest, now_iso

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF-"
_CHUNK_SIZE = 65536


def _year_of(entry: dict) -> str:
    """Year directory segment for an entry ("unknown" when undated)."""
    decision_date = entry.get("decision_date") or ""
    return decision_date[:4] if len(decision_date) >= 4 else "unknown"


def pdf_path_for(entry: dict, data_dir: Path) -> Path:
    """Where an entry's PDF lives: ``<data_dir>/pdfs/<YYYY>/<filename>``."""
    return Path(data_dir) / "pdfs" / _year_of(entry) / entry["filename"]


def text_path_for(entry: dict, data_dir: Path) -> Path:
    """Where an entry's extracted text lives: ``<data_dir>/text/<YYYY>/<id>.txt``."""
    return Path(data_dir) / "text" / _year_of(entry) / f"{entry['id']}.txt"


def download_decision(client: HttpClient, entry: dict, data_dir: Path) -> dict:
    """Download one decision PDF, mutating and returning its manifest *entry*.

    On success: file at :func:`pdf_path_for`, ``download_status="ok"``,
    ``sha256``/``size_bytes``/``downloaded_at`` filled in. Content that does
    not start with ``%PDF-`` yields ``"not_pdf"``; any exception yields
    ``"failed"`` with ``last_error`` set. Partial files are always cleaned up.
    """
    dest = pdf_path_for(entry, data_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.parent / f".partial-{entry['filename']}"

    hasher = hashlib.sha256()
    size = 0
    head = b""
    try:
        response = client.get_stream(entry["url"])
        try:
            with partial.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    if len(head) < len(_PDF_MAGIC):
                        head += chunk[: len(_PDF_MAGIC) - len(head)]
                        if not _PDF_MAGIC.startswith(head):
                            break  # definitely not a PDF; stop streaming
                    hasher.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)
        finally:
            response.close()

        if not head.startswith(_PDF_MAGIC):
            partial.unlink(missing_ok=True)
            entry["download_status"] = "not_pdf"
            entry["last_error"] = (
                "response does not start with %PDF- (likely an HTML error page)"
            )
            logger.warning("Not a PDF: %s", entry["url"])
            return entry

        os.replace(partial, dest)
        entry["sha256"] = hasher.hexdigest()
        entry["size_bytes"] = size
        entry["downloaded_at"] = now_iso()
        entry["download_status"] = "ok"
        entry["last_error"] = None
        logger.info("Downloaded %s (%d bytes)", dest, size)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        entry["download_status"] = "failed"
        entry["last_error"] = str(exc)
        logger.warning("Download failed for %s: %s", entry["url"], exc)
    return entry


def download_all(
    client: HttpClient,
    manifest: Manifest,
    data_dir: Path,
    retry_failed: bool = False,
    save_every: int = 25,
    manifest_path: Optional[Path] = None,
) -> dict:
    """Download every pending entry, checkpointing the manifest as it goes.

    Failures never abort the run: each entry is attempted independently, so an
    interrupted or partially failing run can simply be re-run to resume.
    Returns ``{"attempted", "ok", "failed", "not_pdf"}`` counts.
    """
    counts = {"attempted": 0, "ok": 0, "failed": 0, "not_pdf": 0}
    pending = manifest.pending_downloads(retry_failed=retry_failed)
    logger.info("Downloading %d pending decision(s)", len(pending))

    for index, entry in enumerate(pending, 1):
        counts["attempted"] += 1
        try:
            download_decision(client, entry, data_dir)
        except Exception as exc:  # pragma: no cover - download_decision catches
            entry["download_status"] = "failed"
            entry["last_error"] = str(exc)
        status = entry["download_status"]
        counts[status if status in counts else "failed"] += 1
        if manifest_path is not None and index % save_every == 0:
            manifest.save(manifest_path)

    if manifest_path is not None:
        manifest.save(manifest_path)
    return counts
