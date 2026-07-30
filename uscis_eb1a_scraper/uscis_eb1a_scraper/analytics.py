"""Pure-python analytics over EB-1A AAO case records.

Consumes the case-record dicts produced by the extraction pipeline
(one per decision PDF) and computes the aggregate statistics embedded
in the static dashboard.  No third-party dependencies.
"""
from __future__ import annotations

from datetime import date
from statistics import median

SCHEMA_VERSION = 1

#: Canonical criteria order: (key, plain-language label, 8 CFR cite).
CRITERIA = [
    ("awards", "Awards & prizes", "204.5(h)(3)(i)"),
    ("membership", "Selective memberships", "204.5(h)(3)(ii)"),
    ("published_material", "Published material about you", "204.5(h)(3)(iii)"),
    ("judging", "Judging others' work", "204.5(h)(3)(iv)"),
    ("original_contributions", "Original contributions of major significance", "204.5(h)(3)(v)"),
    ("scholarly_articles", "Scholarly articles", "204.5(h)(3)(vi)"),
    ("artistic_exhibitions", "Artistic exhibitions", "204.5(h)(3)(vii)"),
    ("leading_critical_role", "Leading or critical role", "204.5(h)(3)(viii)"),
    ("high_salary", "High salary", "204.5(h)(3)(ix)"),
    ("commercial_success", "Commercial success in performing arts", "204.5(h)(3)(x)"),
]

CRITERIA_ORDER = [key for key, _, _ in CRITERIA]

FAVORABLE_OUTCOMES = {"sustained", "remanded"}
CORE_OUTCOMES = {"dismissed", "sustained", "remanded"}
CASE_TYPE_ORDER = ["appeal", "motion_reopen", "motion_reconsider", "motion_combined", "unknown"]
LAG_BUCKETS = ["0-30", "31-60", "61-90", "91-180", "180+"]


def _rate(numerator: int, denominator: int) -> float | None:
    """Rate rounded to 4 decimals; None when the denominator is zero."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _outcome(case: dict) -> str:
    return case.get("outcome") or "unknown"


def _month_range(start: str, end: str) -> list[str]:
    """Every YYYY-MM from start to end inclusive."""
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _lag_days(case: dict) -> int | None:
    """Days between the decision and (roughly) its public listing.

    Uses day 15 of the listing month as the listing date; negative lags
    are clamped to 0.  Returns None when either date is missing.
    """
    listing = case.get("listing_month")
    decided = case.get("decision_date")
    if not listing or not decided:
        return None
    listing_mid = date(int(listing[:4]), int(listing[5:7]), 15)
    decision = date(int(decided[:4]), int(decided[5:7]), int(decided[8:10]))
    return max(0, (listing_mid - decision).days)


def _lag_bucket(days: int) -> str:
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    if days <= 180:
        return "91-180"
    return "180+"


def _pct(rate: float) -> str:
    return f"{rate * 100:.1f}"


def _takeaways(
    known: list[dict],
    headline: dict,
    coverage: dict,
    criteria_rows: list[dict],
) -> list[str]:
    """4-6 plain-language sentences filled from the computed numbers."""
    out: list[str] = []
    start_year = coverage["start"][:4] if coverage["start"] else None

    if headline["sustain_rate"] is not None and start_year:
        out.append(
            f"Only {_pct(headline['sustain_rate'])}% of the {len(known)} EB-1A appeals and "
            f"motions with a known outcome since {start_year} were sustained — the large "
            "majority of AAO appeals fail."
        )
    if headline["favorable_rate"] is not None:
        out.append(
            f"Counting remands as partial wins, {_pct(headline['favorable_rate'])}% of "
            "decisions ended favorably (sustained or sent back for a new decision); "
            "the rest were dismissed."
        )

    ruled = [c for c in criteria_rows if (c["met"] + c["not_met"]) > 0]
    if ruled:
        best = max(ruled, key=lambda c: (c["met_rate"], c["met"]))
        worst = min(ruled, key=lambda c: (c["met_rate"], -c["not_met"]))
        if best["key"] != worst["key"]:
            out.append(
                f"When the AAO actually ruled on a criterion, \"{best['label']}\" fared best "
                f"(counted as met in {best['met']} of {best['met'] + best['not_met']} cases), "
                f"while \"{worst['label']}\" fared worst "
                f"({worst['met']} of {worst['met'] + worst['not_met']})."
            )

    appeals = [c for c in known if c.get("case_type") == "appeal"]
    motions = [c for c in known if str(c.get("case_type", "")).startswith("motion")]
    if appeals and motions:
        a_fav = sum(1 for c in appeals if _outcome(c) in FAVORABLE_OUTCOMES)
        m_fav = sum(1 for c in motions if _outcome(c) in FAVORABLE_OUTCOMES)
        out.append(
            f"Appeals ended favorably in {_pct(a_fav / len(appeals))}% of {len(appeals)} cases, "
            f"versus {_pct(m_fav / len(motions))}% for the {len(motions)} motions to reopen or "
            "reconsider — motions rarely change the result."
        )

    at_least_3 = [c for c in known if (c.get("criteria_met_count") or 0) >= 3]
    below_3 = [c for c in known if (c.get("criteria_met_count") or 0) < 3]
    if at_least_3 and below_3:
        hi = sum(1 for c in at_least_3 if _outcome(c) in FAVORABLE_OUTCOMES)
        lo = sum(1 for c in below_3 if _outcome(c) in FAVORABLE_OUTCOMES)
        out.append(
            "The three-criteria threshold is real: cases where the AAO agreed at least 3 "
            f"criteria were met ended favorably {_pct(hi / len(at_least_3))}% of the time, "
            f"versus {_pct(lo / len(below_3))}% when fewer than 3 were credited — and even "
            "meeting 3 criteria is not enough without winning the final merits determination."
        )

    if headline["median_lag_days"] is not None:
        out.append(
            f"Decisions typically show up in the USCIS public reading room about "
            f"{headline['median_lag_days']:.0f} days after they are issued, so recent "
            "months are always undercounted."
        )

    return out[:6]


def compute_analytics(cases: list[dict], generated_at: str) -> dict:
    """Aggregate a list of case records into the dashboard analytics dict."""
    total = len(cases)
    known = [c for c in cases if _outcome(c) != "unknown"]
    unknown_outcome = total - len(known)

    dated = [c for c in cases if c.get("decision_date")]
    months_present = sorted(c["decision_date"][:7] for c in dated)
    start = months_present[0] if months_present else None
    end = months_present[-1] if months_present else None

    # ---- headline -------------------------------------------------------
    sustained = sum(1 for c in known if _outcome(c) == "sustained")
    remanded = sum(1 for c in known if _outcome(c) == "remanded")
    lags = [lag for c in cases if (lag := _lag_days(c)) is not None]
    headline = {
        "sustain_rate": _rate(sustained, len(known)),
        "remand_rate": _rate(remanded, len(known)),
        "favorable_rate": _rate(sustained + remanded, len(known)),
        "median_lag_days": round(float(median(lags)), 4) if lags else None,
    }

    # ---- monthly volume (every month, min -> max, even if 0) ------------
    monthly = []
    if start and end:
        by_month: dict[str, list[dict]] = {}
        for c in dated:
            by_month.setdefault(c["decision_date"][:7], []).append(c)
        for month in _month_range(start, end):
            bucket = by_month.get(month, [])
            d = sum(1 for c in bucket if _outcome(c) == "dismissed")
            s = sum(1 for c in bucket if _outcome(c) == "sustained")
            r = sum(1 for c in bucket if _outcome(c) == "remanded")
            monthly.append({
                "month": month,
                "total": len(bucket),
                "dismissed": d,
                "sustained": s,
                "remanded": r,
                "other": len(bucket) - d - s - r,
            })

    # ---- yearly outcome shares (rates over known outcomes only) ---------
    yearly_outcomes = []
    by_year: dict[int, list[dict]] = {}
    for c in dated:
        by_year.setdefault(int(c["decision_date"][:4]), []).append(c)
    for year in sorted(by_year):
        bucket = by_year[year]
        year_known = [c for c in bucket if _outcome(c) != "unknown"]
        denom = len(year_known)
        d = sum(1 for c in year_known if _outcome(c) == "dismissed")
        s = sum(1 for c in year_known if _outcome(c) == "sustained")
        r = sum(1 for c in year_known if _outcome(c) == "remanded")
        yearly_outcomes.append({
            "year": year,
            "total": len(bucket),
            "rates": {
                "dismissed": _rate(d, denom),
                "sustained": _rate(s, denom),
                "remanded": _rate(r, denom),
                "other": _rate(denom - d - s - r, denom),
            },
        })

    # ---- outcomes by case type (counts; unknown outcomes included) ------
    case_type_outcomes = []
    by_type: dict[str, list[dict]] = {}
    for c in cases:
        by_type.setdefault(c.get("case_type") or "unknown", []).append(c)
    type_order = CASE_TYPE_ORDER + sorted(set(by_type) - set(CASE_TYPE_ORDER))
    for case_type in type_order:
        if case_type not in by_type:
            continue
        bucket = by_type[case_type]
        outcomes: dict[str, int] = {}
        for c in bucket:
            outcomes[_outcome(c)] = outcomes.get(_outcome(c), 0) + 1
        case_type_outcomes.append({
            "case_type": case_type,
            "total": len(bucket),
            "outcomes": dict(sorted(outcomes.items())),
        })

    # ---- per-criterion stats -------------------------------------------
    favorable_cases = [c for c in known if _outcome(c) in FAVORABLE_OUTCOMES]
    criteria_rows = []
    for key, label, cfr in CRITERIA:
        met = not_met = discussed = fav_met = fav_not_met = 0
        for c in cases:
            crit = (c.get("criteria") or {}).get(key) or {}
            if crit.get("discussed"):
                discussed += 1
            if crit.get("met") is True:
                met += 1
            elif crit.get("met") is False:
                not_met += 1
        for c in favorable_cases:
            crit = (c.get("criteria") or {}).get(key) or {}
            if crit.get("met") is True:
                fav_met += 1
            elif crit.get("met") is False:
                fav_not_met += 1
        criteria_rows.append({
            "key": key,
            "label": label,
            "cfr": cfr,
            "discussed": discussed,
            "met": met,
            "not_met": not_met,
            "met_rate": _rate(met, met + not_met),
            "met_rate_in_favorable": _rate(fav_met, fav_met + fav_not_met),
        })

    # ---- co-occurrence of met criteria in favorable cases ---------------
    pair_counts: dict[tuple[int, int], int] = {}
    for c in favorable_cases:
        met_keys = [
            k for k in CRITERIA_ORDER
            if ((c.get("criteria") or {}).get(k) or {}).get("met") is True
        ]
        idxs = [CRITERIA_ORDER.index(k) for k in met_keys]
        for i, a in enumerate(idxs):
            for b in idxs[i + 1:]:
                pair = (min(a, b), max(a, b))
                pair_counts[pair] = pair_counts.get(pair, 0) + 1
    cooccurrence_favorable = [
        {"a": CRITERIA_ORDER[a], "b": CRITERIA_ORDER[b], "count": count}
        for (a, b), count in sorted(pair_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if count > 0
    ]

    # ---- distribution of criteria-met counts ---------------------------
    dist: dict[int, dict[str, int]] = {}
    for c in cases:
        n = int(c.get("criteria_met_count") or 0)
        row = dist.setdefault(n, {"cases": 0, "favorable": 0})
        row["cases"] += 1
        if _outcome(c) in FAVORABLE_OUTCOMES:
            row["favorable"] += 1
    criteria_met_distribution = [
        {"met_count": n, "cases": dist[n]["cases"], "favorable": dist[n]["favorable"]}
        for n in sorted(dist)
    ]

    # ---- final merits ---------------------------------------------------
    fm_reached = [c for c in cases if (c.get("final_merits") or {}).get("reached")]
    final_merits = {
        "reached": len(fm_reached),
        "met": sum(1 for c in fm_reached if (c.get("final_merits") or {}).get("outcome") == "met"),
        "not_met": sum(1 for c in fm_reached if (c.get("final_merits") or {}).get("outcome") == "not_met"),
        "not_reached": total - len(fm_reached),
    }

    # ---- publication-lag histogram --------------------------------------
    lag_hist = {bucket: 0 for bucket in LAG_BUCKETS}
    for lag in lags:
        lag_hist[_lag_bucket(lag)] += 1
    lag_histogram = [{"bucket": b, "count": lag_hist[b]} for b in LAG_BUCKETS]

    # ---- coverage / takeaways / qa --------------------------------------
    coverage = {
        "start": start,
        "end": end,
        "total_cases": total,
        "unknown_outcome": unknown_outcome,
    }
    qa = {
        "outcome_unknown_rate": _rate(unknown_outcome, total) if total else 0.0,
        "extraction_error_count": sum(1 for c in cases if c.get("flags")),
        "low_confidence_count": sum(1 for c in cases if c.get("outcome_confidence") == "low"),
    }
    if qa["outcome_unknown_rate"] is None:
        qa["outcome_unknown_rate"] = 0.0

    return {
        "generated_at": generated_at,
        "schema_version": SCHEMA_VERSION,
        "coverage": coverage,
        "headline": headline,
        "monthly": monthly,
        "yearly_outcomes": yearly_outcomes,
        "case_type_outcomes": case_type_outcomes,
        "criteria": criteria_rows,
        "cooccurrence_favorable": cooccurrence_favorable,
        "criteria_met_distribution": criteria_met_distribution,
        "final_merits": final_merits,
        "lag_histogram": lag_histogram,
        "takeaways": _takeaways(known, headline, coverage, criteria_rows),
        "qa": qa,
    }
