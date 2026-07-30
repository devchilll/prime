"""End-to-end integration test: run_update with REAL extract/analytics/render.

Track-level tests fake the neighboring modules; this suite wires the whole
pipeline together offline. A fake HTTP client serves the captured listing
fixtures, and decision PDFs are hand-crafted minimal PDFs embedding the
fixture decision texts, so pypdf extraction, metadata parsing, analytics, and
dashboard rendering all run for real.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from uscis_eb1a_scraper.pipeline import run_update
from uscis_eb1a_scraper.state import Manifest

FIXTURES = Path(__file__).parent / "fixtures"


def make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF whose text pypdf can extract."""
    parts = ["BT /F1 10 Tf 36 760 Td 12 TL"]
    for line in text.splitlines():
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({esc}) Tj T*")
    parts.append("ET")
    stream = "\n".join(parts).encode("latin-1", "replace")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objs) + 1, xref)
    )
    return bytes(out)


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def iter_content(self, chunk_size: int):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i : i + chunk_size]

    def close(self) -> None:
        pass


class _FakeClient:
    """Serves listing fixtures + synthesized decision PDFs, fully offline."""

    # Specific decisions get specific fixture texts; everything else is a
    # generic dismissed decision.
    SPECIAL = {
        "MAY272025_01B2203.pdf": "decision_sustained.txt",
        "MAY212025_01B2203.pdf": "decision_remand.txt",
    }

    def __init__(self):
        self.page0 = (FIXTURES / "listing_2025_05_page0.html").read_text()
        self.page1 = (FIXTURES / "listing_2025_05_page1.html").read_text()
        self.generic_text = (FIXTURES / "decision_dismissed.txt").read_text()

    def get(self, url, **kwargs):  # connectivity probe
        return SimpleNamespace(status_code=200)

    def get_text(self, url, **kwargs):
        if "m=5" in url and "y=2025" in url:
            return self.page1 if "page=1" in url else self.page0
        return "<html><body>no results</body></html>"

    def get_stream(self, url, **kwargs):
        filename = url.rsplit("/", 1)[-1]
        fixture = self.SPECIAL.get(filename)
        text = (FIXTURES / fixture).read_text() if fixture else self.generic_text
        return _FakeResponse(make_pdf(text))

    def close(self) -> None:
        pass


def _args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        data_dir=tmp_path / "data",
        dashboard_dir=tmp_path / "dashboard",
        category="eb1a",
        window=1,
        start=(2025, 5),
        end=(2025, 5),
        no_download=False,
        delay=None,
    )


def test_update_end_to_end_with_real_modules(tmp_path, capsys):
    rc = run_update(_args(tmp_path), client=_FakeClient())
    out = capsys.readouterr().out
    assert rc == 0
    assert "discovered=13" in out and "downloaded=13" in out
    assert "extracted=13" in out and "dashboard=written" in out

    data = tmp_path / "data"
    manifest = Manifest.load(data / "manifest.jsonl")
    assert len(manifest.entries) == 13
    assert all(e["download_status"] == "ok" for e in manifest.entries.values())
    assert all(e["extraction_status"] == "ok" for e in manifest.entries.values())

    cases = {
        json.loads(line)["id"]: json.loads(line)
        for line in (data / "cases.jsonl").read_text().splitlines()
    }
    assert len(cases) == 13
    # Specific decisions extracted correctly through the PDF round-trip.
    assert cases["MAY272025_01B2203"]["outcome"] == "sustained"
    assert cases["MAY272025_01B2203"]["service_center"] == "Nebraska"
    assert cases["MAY212025_01B2203"]["outcome"] == "remanded"
    assert cases["MAY282025_05B2203"]["outcome"] == "dismissed"
    assert cases["MAY282025_05B2203"]["case_type"] == "appeal"
    assert cases["MAY282025_05B2203"]["in_re_number"] == "32456789"

    analytics = json.loads((data / "analytics.json").read_text())
    assert analytics["coverage"]["total_cases"] == 13
    assert analytics["schema_version"] == 1

    dashboard = (tmp_path / "dashboard" / "index.html").read_text()
    assert "/*__ANALYTICS_JSON__*/" not in dashboard  # placeholders replaced
    assert '"total_cases": 13' in dashboard or '"total_cases":13' in dashboard
    # Self-contained: no external requests beyond uscis.gov case links + SVG ns.
    for match in re.findall(r'https?://[^\s"\'<>]+', dashboard):
        assert "uscis.gov" in match or "w3.org" in match, match


def test_update_rerun_is_idempotent(tmp_path, capsys):
    client = _FakeClient()
    assert run_update(_args(tmp_path), client=client) == 0
    capsys.readouterr()

    assert run_update(_args(tmp_path), client=client) == 0
    out = capsys.readouterr().out
    assert "new=0" in out and "downloaded=0" in out and "extracted=0" in out
    assert "cases=13" in out
