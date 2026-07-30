"""Command-line interface for the USCIS AAO EB-1A pipeline.

Subcommands::

    uscis-eb1a update    [--window N] [--start YYYY-MM] [--end YYYY-MM]
                         [--no-download]        # full incremental run
    uscis-eb1a scrape    --start YYYY-MM --end YYYY-MM
    uscis-eb1a download  [--retry-failed]
    uscis-eb1a extract   [--force] [--qa]
    uscis-eb1a analyze
    uscis-eb1a render
    uscis-eb1a status                            # manifest summary, no network

Common options: ``--category`` (default eb1a), ``--data-dir`` (default
``data``), ``--dashboard-dir`` (default ``dashboard``), ``--delay``, ``-v``.
Paths resolve relative to the current working directory (the project root).

Exit codes: 0 ok, 1 fatal/usage error, 2 partial failures, 3 network
unavailable (www.uscis.gov unreachable -- see INSTRUCTIONS.md).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .client import HttpClient
from .config import CATEGORIES, DASHBOARD_DIR, DATA_DIR
from .state import Manifest, PipelineLock

logger = logging.getLogger(__name__)


def _parse_year_month(value: str) -> Tuple[int, int]:
    """Parse 'YYYY-MM' (or 'YYYY-M') into a (year, month) tuple."""
    try:
        year_s, month_s = value.split("-", 1)
        year, month = int(year_s), int(month_s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {value!r}") from exc
    if not 1 <= month <= 12:
        raise argparse.ArgumentTypeError(f"month out of range in {value!r}")
    return (year, month)


class _ArgumentParser(argparse.ArgumentParser):
    """argparse that exits 1 on usage errors (2 is reserved for partial failures)."""

    def error(self, message: str):  # noqa: D102
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_arg_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--category",
        default="eb1a",
        help=f"Category key or uri_1 value (keys: {', '.join(sorted(CATEGORIES))}).",
    )
    common.add_argument(
        "--data-dir",
        default=DATA_DIR,
        help=f"Data directory (default: {DATA_DIR!r}, relative to cwd).",
    )
    common.add_argument(
        "--dashboard-dir",
        default=DASHBOARD_DIR,
        help=f"Dashboard output directory (default: {DASHBOARD_DIR!r}).",
    )
    common.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Seconds between HTTP requests (politeness throttle).",
    )
    common.add_argument(
        "-v", "--verbose", action="store_true", help="INFO logging to stderr."
    )

    parser = _ArgumentParser(
        prog="uscis-eb1a",
        description=(
            "Incremental scraper + analytics pipeline for USCIS AAO "
            "non-precedent EB-1A decisions."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_update = sub.add_parser(
        "update", parents=[common], help="Scrape window, download, extract, render."
    )
    p_update.add_argument(
        "--window", type=int, default=None, help="Trailing months to re-scan."
    )
    p_update.add_argument(
        "--start", type=_parse_year_month, help="Override window start (YYYY-MM)."
    )
    p_update.add_argument(
        "--end", type=_parse_year_month, help="Override window end (YYYY-MM)."
    )
    p_update.add_argument(
        "--no-download", action="store_true", help="Discover only; skip downloads."
    )
    p_update.set_defaults(func=_cmd_update)

    p_scrape = sub.add_parser(
        "scrape", parents=[common], help="Discover decisions for a month range."
    )
    p_scrape.add_argument("--start", type=_parse_year_month, required=True)
    p_scrape.add_argument("--end", type=_parse_year_month, required=True)
    p_scrape.set_defaults(func=_cmd_scrape)

    p_download = sub.add_parser(
        "download", parents=[common], help="Download pending decision PDFs."
    )
    p_download.add_argument(
        "--retry-failed",
        action="store_true",
        help="Also retry entries previously marked failed/not_pdf.",
    )
    p_download.set_defaults(func=_cmd_download)

    p_extract = sub.add_parser(
        "extract", parents=[common], help="Extract text + case data from PDFs."
    )
    p_extract.add_argument(
        "--force", action="store_true", help="Re-extract already-extracted PDFs."
    )
    p_extract.add_argument(
        "--qa", action="store_true", help="Also write data/extraction_qa.json."
    )
    p_extract.set_defaults(func=_cmd_extract)

    p_analyze = sub.add_parser(
        "analyze", parents=[common], help="Recompute data/analytics.json."
    )
    p_analyze.set_defaults(func=_cmd_analyze)

    p_render = sub.add_parser(
        "render", parents=[common], help="Render dashboard/index.html."
    )
    p_render.set_defaults(func=_cmd_render)

    p_status = sub.add_parser(
        "status", parents=[common], help="Print manifest summary (no network)."
    )
    p_status.set_defaults(func=_cmd_status)

    return parser


# -- helpers ------------------------------------------------------------------


def _make_client(args: argparse.Namespace) -> HttpClient:
    if args.delay is not None:
        return HttpClient(delay_seconds=args.delay)
    return HttpClient()


def _manifest_path(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) / "manifest.jsonl"


# -- subcommand handlers ------------------------------------------------------


def _cmd_update(args: argparse.Namespace) -> int:
    from .pipeline import run_update

    return run_update(args)


def _cmd_scrape(args: argparse.Namespace) -> int:
    from .pipeline import scrape_step

    manifest_path = _manifest_path(args)
    with PipelineLock(Path(args.data_dir)):
        manifest = Manifest.load(manifest_path)
        with _make_client(args) as client:
            result = scrape_step(client, manifest, args.category, args.start, args.end)
        manifest.save(manifest_path)
    print(
        f"scrape: months={result['months_scanned']} "
        f"discovered={result['discovered']} new={result['new']}"
    )
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    from .download import download_all

    manifest_path = _manifest_path(args)
    with PipelineLock(Path(args.data_dir)):
        manifest = Manifest.load(manifest_path)
        with _make_client(args) as client:
            counts = download_all(
                client,
                manifest,
                Path(args.data_dir),
                retry_failed=args.retry_failed,
                manifest_path=manifest_path,
            )
    print(
        f"download: attempted={counts['attempted']} ok={counts['ok']} "
        f"failed={counts['failed']} not_pdf={counts['not_pdf']}"
    )
    return 2 if counts["failed"] + counts["not_pdf"] > 0 else 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from .pipeline import extract_step

    manifest_path = _manifest_path(args)
    with PipelineLock(Path(args.data_dir)):
        manifest = Manifest.load(manifest_path)
        result = extract_step(
            manifest, Path(args.data_dir), force=args.force, qa=args.qa
        )
        manifest.save(manifest_path)
    if "skipped" in result:
        print(f"extract: skipped ({result['skipped']})", file=sys.stderr)
        return 1
    print(
        f"extract: attempted={result['attempted']} ok={result['ok']} "
        f"empty_text={result['empty_text']} error={result['error']} "
        f"cases={result['cases']}"
    )
    return 2 if result["error"] > 0 else 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    from .pipeline import analyze_step

    result = analyze_step(Path(args.data_dir))
    if "skipped" in result:
        print(f"analyze: skipped ({result['skipped']})", file=sys.stderr)
        return 1
    print(f"analyze: cases={result['cases']} analytics=written")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    from .pipeline import render_step

    result = render_step(Path(args.data_dir), Path(args.dashboard_dir))
    if "skipped" in result:
        print(f"render: skipped ({result['skipped']})", file=sys.stderr)
        return 1
    print(f"render: dashboard={result['dashboard']}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    manifest = Manifest.load(_manifest_path(args))
    print(json.dumps(manifest.summary(), indent=2, sort_keys=True))
    return 0


# -- entry points -------------------------------------------------------------


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        return args.func(args)
    except RuntimeError as exc:  # e.g. pipeline lock contention
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # e.g. unknown category
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 1


def main() -> None:  # console-script entry point
    raise SystemExit(run())


if __name__ == "__main__":
    main()
