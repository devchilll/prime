"""Pipeline orchestration: scrape -> download -> extract -> analyze -> render.

Each step is a small function returning a summary dict so the CLI can run them
individually or chained via :func:`run_update`. The extract/analytics/render
implementations live in sibling modules (``extract.py``, ``analytics.py``,
``render.py``) and are imported lazily so the scrape/download half of the
pipeline works even if those modules are absent.

USCIS specifics baked into the flow:

* USCIS posts decisions "within a month" of issuance and backfills earlier
  months, so incremental runs re-scan a trailing window of months
  (:data:`~uscis_eb1a_scraper.config.RESCAN_MONTHS`) rather than only the
  current month -- see :func:`compute_window`.
* www.uscis.gov 403s non-browser traffic and may be blocked entirely by
  sandbox egress policies, so :func:`run_update` probes connectivity first
  and exits with code 3 (plus an actionable message) instead of failing
  noisily halfway through.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .client import HttpClient
from .config import DASHBOARD_DIR, DATA_DIR, DEFAULT_START, LISTING_URL, RESCAN_MONTHS
from .download import download_all, pdf_path_for, text_path_for
from .scraper import AAOScraper, iter_year_months
from .state import Manifest, PipelineLock, atomic_write_text, now_iso

logger = logging.getLogger(__name__)

YearMonth = Tuple[int, int]


# -- helpers ------------------------------------------------------------------


def _month_str(ym: YearMonth) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}"


def _parse_month_str(value: str) -> YearMonth:
    year_s, month_s = value.split("-", 1)
    return (int(year_s), int(month_s))


def _shift_month(ym: YearMonth, delta: int) -> YearMonth:
    index = ym[0] * 12 + (ym[1] - 1) + delta
    return (index // 12, index % 12 + 1)


def _load_jsonl(path: Path) -> List[dict]:
    if not Path(path).exists():
        return []
    out: List[dict] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _case_sort_key(case: dict) -> tuple:
    return (case.get("decision_date") or "9999", case.get("id") or "")


def _write_cases(cases: List[dict], path: Path) -> None:
    lines = [
        json.dumps(case, sort_keys=True, ensure_ascii=False)
        for case in sorted(cases, key=_case_sort_key)
    ]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


# -- steps --------------------------------------------------------------------


def check_connectivity(client: HttpClient) -> bool:
    """True if the AAO listing page is reachable through *client*."""
    try:
        client.get(LISTING_URL)
        return True
    except Exception as exc:
        logger.warning("Connectivity check failed for %s: %s", LISTING_URL, exc)
        return False


def compute_window(
    manifest: Manifest,
    explicit_start: Optional[YearMonth] = None,
    end: Optional[YearMonth] = None,
    window: int = RESCAN_MONTHS,
) -> Tuple[YearMonth, YearMonth]:
    """Work out which (start, end) month range this run should scan.

    *end* defaults to the current month. *start* is the explicit override,
    else the latest month already present in the manifest minus
    ``window - 1`` months (so a run re-scans the trailing window USCIS may
    still be backfilling), floored at :data:`DEFAULT_START`. An empty
    manifest triggers a full backfill from :data:`DEFAULT_START`.
    """
    if end is None:
        from datetime import date

        today = date.today()
        end = (today.year, today.month)

    if explicit_start is not None:
        start = explicit_start
    else:
        months: set[str] = set()
        for entry in manifest.entries.values():
            months.update(entry.get("listing_months") or [])
        if not months:
            start = DEFAULT_START
        else:
            latest = _parse_month_str(max(months))
            start = max(_shift_month(latest, -(window - 1)), DEFAULT_START)

    if start > end:
        start = end
    return (start, end)


def scrape_step(
    client: HttpClient,
    manifest: Manifest,
    category,
    start: YearMonth,
    end: YearMonth,
) -> dict:
    """Scan the listing month range and merge discoveries into the manifest."""
    scraper = AAOScraper(client=client)
    months_scanned = 0
    discovered = 0
    new = 0
    for year, month in iter_year_months(start, end):
        decisions = scraper.fetch_month(category, year, month)
        months_scanned += 1
        discovered += len(decisions)
        new += manifest.merge_discovered(decisions, _month_str((year, month)))
    result = {"months_scanned": months_scanned, "discovered": discovered, "new": new}
    logger.info("Scrape complete: %s", result)
    return result


def extract_step(
    manifest: Manifest, data_dir: Path, force: bool = False, qa: bool = False
) -> dict:
    """Extract text + case metadata from downloaded PDFs; upsert cases.jsonl."""
    try:
        from .extract import build_qa_report, pdf_to_text, text_to_case
    except ImportError:
        logger.warning("extract module not available; skipping extraction")
        return {"skipped": "extract module not available"}

    data_dir = Path(data_dir)
    cases_path = data_dir / "cases.jsonl"
    cases = {case["id"]: case for case in _load_jsonl(cases_path)}

    counts = {"attempted": 0, "ok": 0, "empty_text": 0, "error": 0}
    for entry in manifest.pending_extractions(force=force):
        counts["attempted"] += 1
        pdf_path = pdf_path_for(entry, data_dir)
        try:
            text, pages = pdf_to_text(pdf_path)
        except Exception as exc:
            entry["extraction_status"] = "error"
            entry["last_error"] = str(exc)
            counts["error"] += 1
            logger.warning("Extraction failed for %s: %s", pdf_path, exc)
            continue

        text_path = text_path_for(entry, data_dir)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")

        if not (text or "").strip():
            entry["extraction_status"] = "empty_text"
            entry["extracted_at"] = now_iso()
            counts["empty_text"] += 1
            continue

        meta = {
            "id": entry["id"],
            "url": entry["url"],
            "filename": entry["filename"],
            "category": entry["category"],
            "decision_date": entry["decision_date"],
            "listing_month": (entry.get("listing_months") or [None])[0],
            "sha256": entry["sha256"],
            "pages": pages,
            "extracted_at": now_iso(),
        }
        try:
            case = text_to_case(text, meta)
        except Exception as exc:
            entry["extraction_status"] = "error"
            entry["last_error"] = str(exc)
            counts["error"] += 1
            logger.warning("Case parse failed for %s: %s", entry["id"], exc)
            continue

        cases[case.get("id", entry["id"])] = case
        entry["extraction_status"] = "ok"
        entry["extracted_at"] = meta["extracted_at"]
        entry["last_error"] = None
        counts["ok"] += 1

    all_cases = list(cases.values())
    _write_cases(all_cases, cases_path)
    counts["cases"] = len(all_cases)

    if qa:
        report = build_qa_report(sorted(all_cases, key=_case_sort_key))
        atomic_write_text(
            data_dir / "extraction_qa.json",
            json.dumps(report, indent=2, sort_keys=True) + "\n",
        )
        counts["qa_report"] = "written"
    logger.info("Extraction complete: %s", counts)
    return counts


def analyze_step(data_dir: Path) -> dict:
    """Compute dashboard aggregates from cases.jsonl into analytics.json."""
    try:
        from .analytics import compute_analytics
    except ImportError:
        logger.warning("analytics module not available; skipping analyze")
        return {"skipped": "analytics module not available"}

    data_dir = Path(data_dir)
    cases = _load_jsonl(data_dir / "cases.jsonl")
    analytics = compute_analytics(cases, generated_at=now_iso())
    atomic_write_text(
        data_dir / "analytics.json",
        json.dumps(analytics, indent=2, ensure_ascii=False) + "\n",
    )
    return {"cases": len(cases), "analytics": "written"}


def render_step(data_dir: Path, dashboard_dir: Path) -> dict:
    """Render the static dashboard from analytics.json + cases.jsonl."""
    try:
        from .render import render_dashboard
    except ImportError:
        logger.warning("render module not available; skipping render")
        return {"skipped": "render module not available"}

    data_dir = Path(data_dir)
    analytics_path = data_dir / "analytics.json"
    analytics = (
        json.loads(analytics_path.read_text(encoding="utf-8"))
        if analytics_path.exists()
        else {}
    )
    cases = _load_jsonl(data_dir / "cases.jsonl")
    template_path = Path(__file__).parent / "templates" / "dashboard.html"
    out_path = Path(dashboard_dir) / "index.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_dashboard(analytics, cases, template_path, out_path)
    return {"dashboard": str(out_path)}


# -- full incremental run -----------------------------------------------------


def run_update(args, client: Optional[HttpClient] = None) -> int:
    """Full incremental run: scrape window, download, extract, analyze, render.

    *args* is the CLI namespace (data_dir, dashboard_dir, category, window,
    start, end, no_download, delay). Returns 0 on success, 2 if some items
    failed, 3 if www.uscis.gov is unreachable.
    """
    data_dir = Path(getattr(args, "data_dir", DATA_DIR))
    dashboard_dir = Path(getattr(args, "dashboard_dir", DASHBOARD_DIR))
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / "manifest.jsonl"

    owns_client = client is None
    if client is None:
        delay = getattr(args, "delay", None)
        client = HttpClient(delay_seconds=delay) if delay is not None else HttpClient()

    try:
        with PipelineLock(data_dir):
            if not check_connectivity(client):
                print(
                    "error: www.uscis.gov is unreachable. If you are running in "
                    "a sandbox, allowlist egress to www.uscis.gov (see "
                    "INSTRUCTIONS.md); otherwise check your network/proxy and "
                    "retry.",
                    file=sys.stderr,
                )
                return 3

            manifest = Manifest.load(manifest_path)
            start, end = compute_window(
                manifest,
                explicit_start=getattr(args, "start", None),
                end=getattr(args, "end", None),
                window=getattr(args, "window", None) or RESCAN_MONTHS,
            )
            logger.info(
                "Update window: %s .. %s", _month_str(start), _month_str(end)
            )
            scrape = scrape_step(
                client, manifest, getattr(args, "category", "eb1a"), start, end
            )
            manifest.save(manifest_path)

            downloads = {"attempted": 0, "ok": 0, "failed": 0, "not_pdf": 0}
            if not getattr(args, "no_download", False):
                downloads = download_all(
                    client, manifest, data_dir, manifest_path=manifest_path
                )

            extraction = extract_step(manifest, data_dir)
            manifest.save(manifest_path)

            analysis = analyze_step(data_dir)
            rendered = render_step(data_dir, dashboard_dir)

            cases = analysis.get("cases", extraction.get("cases", 0))
            failed = downloads["failed"] + downloads["not_pdf"] + extraction.get(
                "error", 0
            )
            print(
                f"update: discovered={scrape['discovered']} new={scrape['new']} "
                f"downloaded={downloads['ok']} failed={failed} "
                f"extracted={extraction.get('ok', 0)} cases={cases} "
                f"dashboard={'written' if 'dashboard' in rendered else 'skipped'}"
            )
            return 0 if failed == 0 else 2
    finally:
        if owns_client:
            client.close()
