"""Render the static, self-contained EB-1A dashboard from a template.

The template contains two placeholder tokens sitting inside
``<script type="application/json">`` blocks::

    /*__ANALYTICS_JSON__*/
    /*__CASES_JSON__*/

They are replaced with the JSON-serialized analytics dict and a compact
per-case array.  ``</`` is escaped as ``<\\/`` so untrusted text (e.g. a
weird filename) can never terminate the script block early.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .analytics import CRITERIA_ORDER

ANALYTICS_TOKEN = "/*__ANALYTICS_JSON__*/"
CASES_TOKEN = "/*__CASES_JSON__*/"


def _compact_cases(cases: list[dict]) -> list[dict]:
    """Compact per-case array for the case-explorer table, newest first."""
    compact = []
    for c in cases:
        met = [
            key for key in CRITERIA_ORDER
            if ((c.get("criteria") or {}).get(key) or {}).get("met") is True
        ]
        compact.append({
            "id": c.get("id"),
            "d": c.get("decision_date"),
            "o": c.get("outcome") or "unknown",
            "t": c.get("case_type") or "unknown",
            "m": met,
            "n": int(c.get("criteria_met_count") or 0),
            "u": c.get("url"),
        })
    # Date descending; undated cases last; id as a deterministic tiebreaker.
    compact.sort(
        key=lambda row: (row["d"] is not None, row["d"] or "", row["id"] or ""),
        reverse=True,
    )
    return compact


def _safe_json(obj: object) -> str:
    """JSON with '</' escaped so it can sit inside a <script> block."""
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


def render_dashboard(
    analytics: dict,
    cases: list[dict],
    template_path: Path,
    out_path: Path,
) -> None:
    """Fill the dashboard template and write it to ``out_path`` atomically."""
    template = Path(template_path).read_text(encoding="utf-8")
    for token in (ANALYTICS_TOKEN, CASES_TOKEN):
        if token not in template:
            raise ValueError(f"template is missing placeholder token {token!r}")

    html = template.replace(ANALYTICS_TOKEN, _safe_json(analytics))
    html = html.replace(CASES_TOKEN, _safe_json(_compact_cases(cases)))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(out_path.parent), prefix=out_path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(html)
        os.replace(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
