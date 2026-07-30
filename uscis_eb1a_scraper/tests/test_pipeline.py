"""Tests for the pipeline orchestration (offline: fake client + fake modules)."""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest

from uscis_eb1a_scraper.config import DEFAULT_START
from uscis_eb1a_scraper.models import Decision
from uscis_eb1a_scraper.pipeline import (
    analyze_step,
    check_connectivity,
    compute_window,
    extract_step,
    render_step,
    run_update,
    scrape_step,
)
from uscis_eb1a_scraper.state import Manifest

FIXTURES = Path(__file__).parent / "fixtures"

ERR = (
    "https://www.uscis.gov/sites/default/files/err/"
    "B2%20-%20Aliens%20with%20Extraordinary%20Ability/Decisions_Issued_in_2025/"
)

PDF_BYTES = b"%PDF-1.7\nfake decision pdf\n%%EOF\n"


# -- fakes --------------------------------------------------------------------


class FakeStream:
    def __init__(self, payload: bytes):
        self._payload = payload

    def iter_content(self, chunk_size=65536):
        yield self._payload

    def close(self):
        pass


class FakeClient:
    """Serves the May-2025 listing fixtures and PDF bytes for any /err/ URL."""

    def __init__(self, reachable: bool = True):
        self.reachable = reachable
        self.urls: list[str] = []
        self.pages = {
            0: (FIXTURES / "listing_2025_05_page0.html").read_text(),
            1: (FIXTURES / "listing_2025_05_page1.html").read_text(),
        }

    def get(self, url, **kwargs):
        if not self.reachable:
            raise ConnectionError("egress blocked")
        return types.SimpleNamespace(status_code=200)

    def get_text(self, url, **kwargs):
        self.urls.append(url)
        if "m=5&y=2025" in url:
            page = int(url.rsplit("page=", 1)[-1]) if "page=" in url else 0
            return self.pages.get(page, "<html><body>no results</body></html>")
        return "<html><body>no results</body></html>"

    def get_stream(self, url, **kwargs):
        self.urls.append(url)
        return FakeStream(PDF_BYTES)

    def close(self):
        pass


def _fake_module(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def fake_pipeline_modules(monkeypatch):
    """Install fake extract/analytics/render modules (Tracks B and C)."""

    def pdf_to_text(pdf_path):
        return (f"NON-PRECEDENT DECISION text for {Path(pdf_path).stem}", 3)

    def text_to_case(text, meta):
        return {
            "id": meta["id"],
            "decision_date": meta["decision_date"],
            "listing_month": meta["listing_month"],
            "pages": meta["pages"],
            "outcome": "dismissed",
        }

    def build_qa_report(cases):
        return {"total": len(cases)}

    def compute_analytics(cases, generated_at):
        return {"total_cases": len(cases), "generated_at": generated_at}

    calls = {}

    def render_dashboard(analytics, cases, template_path, out_path):
        calls["render"] = {
            "analytics": analytics,
            "cases": cases,
            "template_path": Path(template_path),
            "out_path": Path(out_path),
        }
        Path(out_path).write_text("<html>dashboard</html>", encoding="utf-8")

    monkeypatch.setitem(
        sys.modules,
        "uscis_eb1a_scraper.extract",
        _fake_module(
            "uscis_eb1a_scraper.extract",
            pdf_to_text=pdf_to_text,
            text_to_case=text_to_case,
            build_qa_report=build_qa_report,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "uscis_eb1a_scraper.analytics",
        _fake_module("uscis_eb1a_scraper.analytics", compute_analytics=compute_analytics),
    )
    monkeypatch.setitem(
        sys.modules,
        "uscis_eb1a_scraper.render",
        _fake_module("uscis_eb1a_scraper.render", render_dashboard=render_dashboard),
    )
    return calls


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- compute_window -----------------------------------------------------------


def test_compute_window_empty_manifest_starts_at_default():
    start, end = compute_window(Manifest(), end=(2026, 7))
    assert start == DEFAULT_START
    assert end == (2026, 7)


def test_compute_window_rescans_trailing_window():
    manifest = Manifest()
    for month in ("2026-05", "2026-06"):
        manifest.merge_discovered(
            [
                Decision.from_url(
                    ERR + f"X{month.replace('-', '')}.pdf", category_key="eb1a"
                )
            ],
            month,
        )
    start, end = compute_window(manifest, end=(2026, 7), window=4)
    assert start == (2026, 3)  # latest seen month 2026-06 minus (4 - 1)
    assert end == (2026, 7)


def test_compute_window_explicit_start_wins():
    manifest = Manifest()
    manifest.merge_discovered(
        [Decision.from_url(ERR + "A.pdf", category_key="eb1a")], "2026-06"
    )
    start, end = compute_window(manifest, explicit_start=(2021, 2), end=(2026, 7))
    assert (start, end) == ((2021, 2), (2026, 7))


def test_compute_window_floors_at_default_start():
    manifest = Manifest()
    manifest.merge_discovered(
        [Decision.from_url(ERR + "A.pdf", category_key="eb1a")], "2020-02"
    )
    start, _ = compute_window(manifest, end=(2020, 6), window=12)
    assert start == DEFAULT_START


def test_compute_window_end_defaults_to_current_month():
    from datetime import date

    _, end = compute_window(Manifest())
    today = date.today()
    assert end == (today.year, today.month)


# -- connectivity + scrape ----------------------------------------------------


def test_check_connectivity():
    assert check_connectivity(FakeClient(reachable=True)) is True
    assert check_connectivity(FakeClient(reachable=False)) is False


def test_scrape_step_merges_fixtures_and_is_idempotent():
    manifest = Manifest()
    result = scrape_step(FakeClient(), manifest, "eb1a", (2025, 5), (2025, 5))
    assert result == {"months_scanned": 1, "discovered": 13, "new": 13}
    assert len(manifest) == 13
    assert all(
        e["listing_months"] == ["2025-05"] for e in manifest.entries.values()
    )

    rerun = scrape_step(FakeClient(), manifest, "eb1a", (2025, 5), (2025, 5))
    assert rerun == {"months_scanned": 1, "discovered": 13, "new": 0}
    assert len(manifest) == 13


# -- extract / analyze / render ----------------------------------------------


def _downloaded_manifest(n: int = 2) -> Manifest:
    manifest = Manifest()
    names = ["MAY122025_02B2203.pdf", "MAY142025_01B2203.pdf", "MAY152025_02B2203.pdf"]
    manifest.merge_discovered(
        [Decision.from_url(ERR + name, category_key="eb1a") for name in names[:n]],
        "2025-05",
    )
    for entry in manifest.entries.values():
        entry["download_status"] = "ok"
        entry["sha256"] = "deadbeef"
        entry["size_bytes"] = 123
    return manifest


def test_extract_step_writes_text_and_cases(fake_pipeline_modules, tmp_path):
    manifest = _downloaded_manifest(2)
    result = extract_step(manifest, tmp_path, qa=True)

    assert result["attempted"] == 2
    assert result["ok"] == 2
    assert result["error"] == 0
    assert result["cases"] == 2

    assert (tmp_path / "text" / "2025" / "MAY122025_02B2203.txt").exists()
    cases = _load_jsonl(tmp_path / "cases.jsonl")
    assert [c["id"] for c in cases] == ["MAY122025_02B2203", "MAY142025_01B2203"]
    assert all(c["listing_month"] == "2025-05" for c in cases)

    for entry in manifest.entries.values():
        assert entry["extraction_status"] == "ok"
        assert entry["extracted_at"].endswith("Z")

    qa = json.loads((tmp_path / "extraction_qa.json").read_text())
    assert qa == {"total": 2}

    # Re-run without force: nothing pending, cases preserved (upsert).
    rerun = extract_step(manifest, tmp_path)
    assert rerun["attempted"] == 0
    assert len(_load_jsonl(tmp_path / "cases.jsonl")) == 2


def test_extract_step_handles_empty_text_and_errors(monkeypatch, tmp_path):
    def pdf_to_text(pdf_path):
        name = Path(pdf_path).name
        if "MAY122025" in name:
            return ("", 0)  # empty text
        raise RuntimeError("corrupt pdf")

    monkeypatch.setitem(
        sys.modules,
        "uscis_eb1a_scraper.extract",
        _fake_module(
            "uscis_eb1a_scraper.extract",
            pdf_to_text=pdf_to_text,
            text_to_case=lambda text, meta: {"id": meta["id"]},
            build_qa_report=lambda cases: {},
        ),
    )
    manifest = _downloaded_manifest(2)
    result = extract_step(manifest, tmp_path)

    assert result["attempted"] == 2
    assert result["empty_text"] == 1
    assert result["error"] == 1
    statuses = {
        e["filename"]: e["extraction_status"] for e in manifest.entries.values()
    }
    assert statuses["MAY122025_02B2203.pdf"] == "empty_text"
    assert statuses["MAY142025_01B2203.pdf"] == "error"
    assert (
        "corrupt pdf"
        in manifest.entries["MAY142025_01B2203.pdf"]["last_error"]
    )


def test_extract_step_skips_when_module_missing(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "uscis_eb1a_scraper.extract", None)
    result = extract_step(_downloaded_manifest(1), tmp_path)
    assert result == {"skipped": "extract module not available"}


def test_analyze_step_writes_analytics(fake_pipeline_modules, tmp_path):
    cases = [{"id": "A", "decision_date": "2025-05-12"}]
    (tmp_path / "cases.jsonl").write_text(
        "\n".join(json.dumps(c) for c in cases) + "\n"
    )
    result = analyze_step(tmp_path)
    assert result == {"cases": 1, "analytics": "written"}
    analytics = json.loads((tmp_path / "analytics.json").read_text())
    assert analytics["total_cases"] == 1
    assert analytics["generated_at"].endswith("Z")


def test_analyze_step_skips_when_module_missing(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "uscis_eb1a_scraper.analytics", None)
    assert "skipped" in analyze_step(tmp_path)


def test_render_step_wires_paths(fake_pipeline_modules, tmp_path):
    data_dir = tmp_path / "data"
    dashboard_dir = tmp_path / "dashboard"
    data_dir.mkdir()
    (data_dir / "analytics.json").write_text(json.dumps({"total_cases": 1}))
    (data_dir / "cases.jsonl").write_text(json.dumps({"id": "A"}) + "\n")

    result = render_step(data_dir, dashboard_dir)

    out_path = dashboard_dir / "index.html"
    assert result == {"dashboard": str(out_path)}
    assert out_path.read_text() == "<html>dashboard</html>"

    call = fake_pipeline_modules["render"]
    assert call["analytics"] == {"total_cases": 1}
    assert call["cases"] == [{"id": "A"}]
    assert call["template_path"].name == "dashboard.html"
    assert call["template_path"].parent.name == "templates"
    assert call["out_path"] == out_path


def test_render_step_skips_when_module_missing(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "uscis_eb1a_scraper.render", None)
    assert "skipped" in render_step(tmp_path, tmp_path / "dash")


# -- run_update ---------------------------------------------------------------


def _update_args(tmp_path: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        data_dir=str(tmp_path / "data"),
        dashboard_dir=str(tmp_path / "dashboard"),
        category="eb1a",
        window=4,
        start=(2025, 5),
        end=(2025, 5),
        no_download=False,
        delay=0.0,
        verbose=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_run_update_end_to_end(fake_pipeline_modules, tmp_path, capsys):
    args = _update_args(tmp_path)
    client = FakeClient()

    code = run_update(args, client=client)

    assert code == 0
    out = capsys.readouterr().out
    assert (
        "update: discovered=13 new=13 downloaded=13 failed=0 "
        "extracted=13 cases=13 dashboard=written"
    ) in out

    data_dir = tmp_path / "data"
    manifest = Manifest.load(data_dir / "manifest.jsonl")
    assert len(manifest) == 13
    assert all(
        e["download_status"] == "ok" and e["extraction_status"] == "ok"
        for e in manifest.entries.values()
    )
    assert len(list((data_dir / "pdfs" / "2025").glob("*.pdf"))) == 13
    assert len(_load_jsonl(data_dir / "cases.jsonl")) == 13
    assert (data_dir / "analytics.json").exists()
    assert (tmp_path / "dashboard" / "index.html").exists()
    assert not (data_dir / ".lock").exists(), "lock must be released"

    # Second run discovers the same 13, adds none, redownloads nothing.
    code = run_update(_update_args(tmp_path), client=FakeClient())
    assert code == 0
    assert "discovered=13 new=0 downloaded=0" in capsys.readouterr().out


def test_run_update_unreachable_returns_3(fake_pipeline_modules, tmp_path, capsys):
    code = run_update(_update_args(tmp_path), client=FakeClient(reachable=False))
    assert code == 3
    err = capsys.readouterr().err
    assert "www.uscis.gov" in err
    assert not (tmp_path / "data" / ".lock").exists()


def test_run_update_no_download_skips_downloads(fake_pipeline_modules, tmp_path, capsys):
    client = FakeClient()
    code = run_update(_update_args(tmp_path, no_download=True), client=client)
    assert code == 0
    assert "downloaded=0" in capsys.readouterr().out
    assert not any("/err/" in u for u in client.urls if "uri_1" not in u)
    manifest = Manifest.load(tmp_path / "data" / "manifest.jsonl")
    assert len(manifest.pending_downloads()) == 13
