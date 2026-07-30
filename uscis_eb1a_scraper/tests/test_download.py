"""Tests for streamed, validated PDF downloading (offline, fake client)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from uscis_eb1a_scraper.download import (
    download_all,
    download_decision,
    pdf_path_for,
    text_path_for,
)
from uscis_eb1a_scraper.models import Decision
from uscis_eb1a_scraper.state import Manifest

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
HTML_BYTES = b"<html><body><h1>Access Denied</h1></body></html>"

ERR = (
    "https://www.uscis.gov/sites/default/files/err/"
    "B2%20-%20Aliens%20with%20Extraordinary%20Ability/Decisions_Issued_in_2025/"
)


class FakeResponse:
    def __init__(self, payload: bytes, chunk_size: int = 7):
        self._payload = payload
        self._chunk_size = chunk_size
        self.closed = False

    def iter_content(self, chunk_size=65536):
        # Deliberately tiny chunks so the %PDF- magic spans several chunks.
        for i in range(0, len(self._payload), self._chunk_size):
            yield self._payload[i : i + self._chunk_size]

    def close(self):
        self.closed = True


class FakeClient:
    """Maps URL -> bytes payload or an Exception to raise."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.requested: list[str] = []

    def get_stream(self, url, **kwargs):
        self.requested.append(url)
        payload = self.responses[url]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)


def make_entry(filename: str = "MAY282025_05B2203.pdf") -> dict:
    manifest = Manifest()
    manifest.merge_discovered(
        [Decision.from_url(ERR + filename, category_key="eb1a")], "2025-05"
    )
    return next(iter(manifest.entries.values()))


def test_path_helpers(tmp_path: Path):
    entry = make_entry()
    assert pdf_path_for(entry, tmp_path) == (
        tmp_path / "pdfs" / "2025" / "MAY282025_05B2203.pdf"
    )
    assert text_path_for(entry, tmp_path) == (
        tmp_path / "text" / "2025" / "MAY282025_05B2203.txt"
    )
    undated = dict(entry, decision_date=None)
    assert pdf_path_for(undated, tmp_path).parent.name == "unknown"


def test_download_success(tmp_path: Path):
    entry = make_entry()
    client = FakeClient({entry["url"]: PDF_BYTES})

    result = download_decision(client, entry, tmp_path)

    assert result is entry
    dest = tmp_path / "pdfs" / "2025" / "MAY282025_05B2203.pdf"
    assert dest.read_bytes() == PDF_BYTES
    assert entry["download_status"] == "ok"
    assert entry["sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
    assert entry["size_bytes"] == len(PDF_BYTES)
    assert entry["downloaded_at"].endswith("Z")
    assert entry["last_error"] is None
    assert not list(dest.parent.glob(".partial-*"))


def test_download_html_marks_not_pdf(tmp_path: Path):
    entry = make_entry()
    client = FakeClient({entry["url"]: HTML_BYTES})

    download_decision(client, entry, tmp_path)

    assert entry["download_status"] == "not_pdf"
    assert "%PDF-" in entry["last_error"]
    assert entry["sha256"] is None
    year_dir = tmp_path / "pdfs" / "2025"
    assert not any(year_dir.iterdir()), "no file (partial or final) may remain"


def test_download_exception_marks_failed(tmp_path: Path):
    entry = make_entry()
    client = FakeClient({entry["url"]: ConnectionError("connection reset")})

    download_decision(client, entry, tmp_path)

    assert entry["download_status"] == "failed"
    assert "connection reset" in entry["last_error"]
    year_dir = tmp_path / "pdfs" / "2025"
    assert not any(year_dir.iterdir())


def test_download_all_continues_past_failures_and_checkpoints(tmp_path: Path):
    manifest = Manifest()
    manifest.merge_discovered(
        [
            Decision.from_url(ERR + name, category_key="eb1a")
            for name in (
                "MAY122025_02B2203.pdf",
                "MAY142025_01B2203.pdf",
                "MAY152025_02B2203.pdf",
                "MAY162025_04B2203.pdf",
            )
        ],
        "2025-05",
    )
    responses = {
        e["url"]: PDF_BYTES for e in manifest.entries.values()
    }
    responses[ERR + "MAY142025_01B2203.pdf"] = ConnectionError("boom")
    responses[ERR + "MAY152025_02B2203.pdf"] = HTML_BYTES
    client = FakeClient(responses)

    manifest_path = tmp_path / "manifest.jsonl"
    counts = download_all(
        client,
        manifest,
        tmp_path,
        save_every=1,
        manifest_path=manifest_path,
    )

    assert counts == {"attempted": 4, "ok": 2, "failed": 1, "not_pdf": 1}
    assert manifest_path.exists(), "manifest checkpoint must be written"

    reloaded = Manifest.load(manifest_path)
    statuses = {e["filename"]: e["download_status"] for e in reloaded.entries.values()}
    assert statuses == {
        "MAY122025_02B2203.pdf": "ok",
        "MAY142025_01B2203.pdf": "failed",
        "MAY152025_02B2203.pdf": "not_pdf",
        "MAY162025_04B2203.pdf": "ok",
    }
    assert not manifest.pending_downloads()
    assert len(manifest.pending_downloads(retry_failed=True)) == 2


def test_download_all_nothing_pending(tmp_path: Path):
    manifest = Manifest()
    counts = download_all(FakeClient({}), manifest, tmp_path)
    assert counts == {"attempted": 0, "ok": 0, "failed": 0, "not_pdf": 0}
