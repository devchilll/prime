"""Command-line interface for the USCIS AAO EB-1A scraper.

Examples::

    # List EB-1A decisions for May 2025 (default category is eb1a):
    python -m uscis_eb1a_scraper --year 2025 --month 5

    # List EB-1A decisions across a whole range, as JSON:
    python -m uscis_eb1a_scraper --start 2024-01 --end 2025-12 --json

    # Download the PDFs into ./decisions:
    python -m uscis_eb1a_scraper --year 2025 --month 5 --download --out-dir decisions

    # NIW instead of EB-1A:
    python -m uscis_eb1a_scraper --category niw --year 2025 --month 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional, Tuple

from .config import CATEGORIES, get_category
from .models import Decision
from .scraper import AAOScraper


def _parse_year_month(value: str) -> Tuple[int, int]:
    """Parse 'YYYY-MM' (or 'YYYY-M') into a (year, month) tuple."""
    try:
        year_s, month_s = value.split("-", 1)
        year, month = int(year_s), int(month_s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM, got {value!r}"
        ) from exc
    if not 1 <= month <= 12:
        raise argparse.ArgumentTypeError(f"month out of range in {value!r}")
    return (year, month)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uscis_eb1a_scraper",
        description=(
            "Discover and download USCIS AAO non-precedent decisions "
            "(EB-1A by default) by category, month, and year."
        ),
    )
    parser.add_argument(
        "--category",
        default="eb1a",
        help=(
            "Category key or uri_1 value "
            f"(keys: {', '.join(sorted(CATEGORIES))}; default: eb1a)."
        ),
    )

    # Single month vs. a range.
    parser.add_argument("--year", type=int, help="Year filter (y), e.g. 2025.")
    parser.add_argument("--month", type=int, help="Month filter (m), 1-12.")
    parser.add_argument(
        "--start", type=_parse_year_month, help="Range start as YYYY-MM (inclusive)."
    )
    parser.add_argument(
        "--end", type=_parse_year_month, help="Range end as YYYY-MM (inclusive)."
    )

    parser.add_argument(
        "--download", action="store_true", help="Download decision PDFs."
    )
    parser.add_argument(
        "--out-dir", default="decisions", help="Output directory for --download."
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit results as JSON to stdout."
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Seconds between requests (politeness throttle).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging to stderr."
    )
    return parser


def _resolve_window(
    args: argparse.Namespace,
) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """Work out the (start, end) year-month window from CLI args."""
    if args.start or args.end:
        if not (args.start and args.end):
            raise SystemExit("--start and --end must be given together")
        return args.start, args.end
    if args.year and args.month:
        return (args.year, args.month), (args.year, args.month)
    raise SystemExit("provide either --year/--month or --start/--end")


def run(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        category = get_category(args.category)
    except ValueError as exc:
        raise SystemExit(str(exc))

    start, end = _resolve_window(args)

    from .client import HttpClient

    client = HttpClient(delay_seconds=args.delay) if args.delay is not None else HttpClient()
    decisions: List[Decision] = []
    with AAOScraper(client=client) as scraper:
        decisions = scraper.fetch_range(category, start, end)
        if args.download:
            for decision in decisions:
                scraper.download(decision, args.out_dir)

    if args.json:
        json.dump([d.to_dict() for d in decisions], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(
            f"Found {len(decisions)} {category.key.upper()} decision(s) "
            f"({category.name}):"
        )
        for d in decisions:
            date_s = d.decision_date.isoformat() if d.decision_date else "????-??-??"
            print(f"  {date_s}  {d.filename}  {d.url}")
        if args.download:
            print(f"\nDownloaded PDFs into: {args.out_dir}/")

    return 0


def main() -> None:  # console-script entry point
    raise SystemExit(run())


if __name__ == "__main__":
    main()
