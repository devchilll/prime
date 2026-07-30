"""Tests for the manifest state store and the pipeline lock (offline)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from uscis_eb1a_scraper.models import Decision
from uscis_eb1a_scraper.state import Manifest, PipelineLock, now_iso

ERR = (
    "https://www.uscis.gov/sites/default/files/err/"
    "B2%20-%20Aliens%20with%20Extraordinary%20Ability/Decisions_Issued_in_2025/"
)


def make_decision(filename: str, url: str | None = None) -> Decision:
    return Decision.from_url(
        url or (ERR + filename), category_key="eb1a", listing_year=2025, listing_month=5
    )


# -- merge_discovered ---------------------------------------------------------


def test_merge_discovered_adds_new_entries():
    manifest = Manifest()
    decisions = [
        make_decision("MAY282025_05B2203.pdf"),
        make_decision("MAY272025_01B2203.pdf"),
    ]
    added = manifest.merge_discovered(decisions, "2025-05")
    assert added == 2
    assert len(manifest) == 2

    entry = manifest.entries["MAY282025_05B2203.pdf"]
    assert entry["id"] == "MAY282025_05B2203"
    assert entry["filename"] == "MAY282025_05B2203.pdf"
    assert entry["url"].endswith("MAY282025_05B2203.pdf")
    assert entry["category"] == "eb1a"
    assert entry["decision_date"] == "2025-05-28"
    assert entry["listing_months"] == ["2025-05"]
    assert entry["download_status"] == "pending"
    assert entry["extraction_status"] == "pending"
    assert entry["sha256"] is None
    assert entry["size_bytes"] is None
    assert entry["downloaded_at"] is None
    assert entry["extracted_at"] is None
    assert entry["last_error"] is None
    assert entry["first_seen_at"].endswith("Z")


def test_merge_discovered_existing_appends_listing_month_once():
    manifest = Manifest()
    decision = make_decision("MAY282025_05B2203.pdf")
    assert manifest.merge_discovered([decision], "2025-05") == 1

    # Re-seen in a later month: no new entry, month appended.
    assert manifest.merge_discovered([decision], "2025-06") == 0
    entry = manifest.entries["MAY282025_05B2203.pdf"]
    assert entry["listing_months"] == ["2025-05", "2025-06"]

    # Same month again: no duplicate month.
    assert manifest.merge_discovered([decision], "2025-06") == 0
    assert entry["listing_months"] == ["2025-05", "2025-06"]
    assert len(manifest) == 1


def test_merge_discovered_same_filename_different_url_keeps_both():
    manifest = Manifest()
    d1 = make_decision("MAY282025_05B2203.pdf")
    other_url = (
        "https://www.uscis.gov/sites/default/files/err/"
        "B2/Decisions_Issued_in_2025/MAY282025_05B2203.pdf"
    )
    d2 = make_decision("MAY282025_05B2203.pdf", url=other_url)
    assert manifest.merge_discovered([d1], "2025-05") == 1
    assert manifest.merge_discovered([d2], "2025-05") == 1
    assert len(manifest) == 2
    assert other_url in manifest.entries
    # Re-merging either is idempotent.
    assert manifest.merge_discovered([d1, d2], "2025-05") == 0
    assert len(manifest) == 2


# -- save / load --------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path: Path):
    manifest = Manifest()
    manifest.merge_discovered(
        [make_decision("MAY282025_05B2203.pdf"), make_decision("MAY122025_02B2203.pdf")],
        "2025-05",
    )
    path = tmp_path / "manifest.jsonl"
    manifest.save(path)

    assert path.exists()
    assert not list(tmp_path.glob("*.tmp")), "atomic tmp file must be cleaned up"

    reloaded = Manifest.load(path)
    assert len(reloaded) == 2
    assert reloaded.entries == manifest.entries


def test_load_missing_file_is_empty(tmp_path: Path):
    manifest = Manifest.load(tmp_path / "nope.jsonl")
    assert len(manifest) == 0
    assert manifest.summary()["total"] == 0


def test_save_is_sorted_and_deterministic(tmp_path: Path):
    m1 = Manifest()
    m1.merge_discovered(
        [
            make_decision("MAY282025_05B2203.pdf"),
            make_decision("MAY122025_02B2203.pdf"),
            make_decision("weird-name.pdf"),  # unparseable date -> sorts last
        ],
        "2025-05",
    )
    # Same entries, different insertion order.
    m2 = Manifest()
    m2.merge_discovered(
        [
            make_decision("weird-name.pdf"),
            make_decision("MAY122025_02B2203.pdf"),
            make_decision("MAY282025_05B2203.pdf"),
        ],
        "2025-05",
    )
    # Normalise the timestamps so byte comparison is meaningful.
    for m in (m1, m2):
        for entry in m.entries.values():
            entry["first_seen_at"] = "2025-06-01T00:00:00Z"

    p1, p2 = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    m1.save(p1)
    m2.save(p2)
    assert p1.read_bytes() == p2.read_bytes()

    dates = [json.loads(line)["decision_date"] for line in p1.read_text().splitlines()]
    assert dates == ["2025-05-12", "2025-05-28", None]  # undated last


# -- pending filters ----------------------------------------------------------


def _manifest_with_statuses() -> Manifest:
    manifest = Manifest()
    manifest.merge_discovered(
        [
            make_decision("MAY122025_02B2203.pdf"),  # stays pending
            make_decision("MAY142025_01B2203.pdf"),  # -> ok, extraction ok
            make_decision("MAY152025_02B2203.pdf"),  # -> ok, extraction pending
            make_decision("MAY162025_04B2203.pdf"),  # -> failed
            make_decision("MAY192025_01B2203.pdf"),  # -> not_pdf
        ],
        "2025-05",
    )
    e = manifest.entries
    e["MAY142025_01B2203.pdf"]["download_status"] = "ok"
    e["MAY142025_01B2203.pdf"]["extraction_status"] = "ok"
    e["MAY152025_02B2203.pdf"]["download_status"] = "ok"
    e["MAY162025_04B2203.pdf"]["download_status"] = "failed"
    e["MAY192025_01B2203.pdf"]["download_status"] = "not_pdf"
    return manifest


def test_pending_downloads_filters():
    manifest = _manifest_with_statuses()
    pending = manifest.pending_downloads()
    assert [e["filename"] for e in pending] == ["MAY122025_02B2203.pdf"]

    retry = manifest.pending_downloads(retry_failed=True)
    assert sorted(e["filename"] for e in retry) == [
        "MAY122025_02B2203.pdf",
        "MAY162025_04B2203.pdf",
        "MAY192025_01B2203.pdf",
    ]


def test_pending_extractions_filters():
    manifest = _manifest_with_statuses()
    pending = manifest.pending_extractions()
    assert [e["filename"] for e in pending] == ["MAY152025_02B2203.pdf"]

    forced = manifest.pending_extractions(force=True)
    assert sorted(e["filename"] for e in forced) == [
        "MAY142025_01B2203.pdf",
        "MAY152025_02B2203.pdf",
    ]


def test_summary_counts_and_months():
    manifest = _manifest_with_statuses()
    manifest.merge_discovered([make_decision("MAY142025_01B2203.pdf")], "2025-06")
    summary = manifest.summary()
    assert summary["total"] == 5
    assert summary["download_status"]["ok"] == 2
    assert summary["download_status"]["pending"] == 1
    assert summary["download_status"]["failed"] == 1
    assert summary["download_status"]["not_pdf"] == 1
    assert summary["extraction_status"]["ok"] == 1
    assert summary["months"]["min"] == "2025-05"
    assert summary["months"]["max"] == "2025-06"


# -- PipelineLock -------------------------------------------------------------


def test_lock_acquire_and_release(tmp_path: Path):
    lock_path = tmp_path / ".lock"
    with PipelineLock(tmp_path):
        assert lock_path.exists()
        info = json.loads(lock_path.read_text())
        assert isinstance(info["pid"], int)
        assert info["started_at"].endswith("Z")
    assert not lock_path.exists()


def test_lock_released_on_exception(tmp_path: Path):
    with pytest.raises(ValueError):
        with PipelineLock(tmp_path):
            raise ValueError("boom")
    assert not (tmp_path / ".lock").exists()


def test_lock_contention_fresh_lock_raises(tmp_path: Path):
    (tmp_path / ".lock").write_text(
        json.dumps({"pid": 12345, "started_at": now_iso()})
    )
    with pytest.raises(RuntimeError):
        PipelineLock(tmp_path).__enter__()
    # The foreign lock must not have been removed by the failed acquire.
    assert (tmp_path / ".lock").exists()


def test_lock_stale_is_stolen(tmp_path: Path):
    stale = (
        (datetime.now(timezone.utc) - timedelta(hours=7))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    (tmp_path / ".lock").write_text(json.dumps({"pid": 999, "started_at": stale}))
    with PipelineLock(tmp_path):
        info = json.loads((tmp_path / ".lock").read_text())
        assert info["pid"] == os.getpid()  # rewritten by the stealing run
    assert not (tmp_path / ".lock").exists()
