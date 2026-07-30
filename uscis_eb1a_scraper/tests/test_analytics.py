"""Hand-computed assertions for compute_analytics against cases_sample.jsonl."""
import json
from pathlib import Path

import pytest

from uscis_eb1a_scraper.analytics import compute_analytics

FIXTURE = Path(__file__).parent / "fixtures" / "cases_sample.jsonl"
GENERATED_AT = "2026-07-30T12:00:00Z"


@pytest.fixture(scope="module")
def cases():
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def analytics(cases):
    return compute_analytics(cases, GENERATED_AT)


def test_top_level_shape(analytics):
    assert analytics["generated_at"] == GENERATED_AT
    assert analytics["schema_version"] == 1
    for key in (
        "coverage", "headline", "monthly", "yearly_outcomes", "case_type_outcomes",
        "criteria", "cooccurrence_favorable", "criteria_met_distribution",
        "final_merits", "lag_histogram", "takeaways", "qa",
    ):
        assert key in analytics


def test_coverage(analytics):
    cov = analytics["coverage"]
    assert cov["total_cases"] == 12
    assert cov["unknown_outcome"] == 1
    assert cov["start"] == "2024-01"
    assert cov["end"] == "2025-06"


def test_headline_rates_exclude_unknown(analytics):
    # 12 cases, 1 unknown outcome -> denominator 11.
    head = analytics["headline"]
    assert head["sustain_rate"] == round(1 / 11, 4)
    assert head["remand_rate"] == round(1 / 11, 4)
    assert head["favorable_rate"] == round(2 / 11, 4)


def test_median_lag_days(analytics):
    # Hand-computed lags (day 15 of listing month minus decision date):
    # 36, 10, 27, 15, 33, 25, 29, 18, 36, 25, 18, 15 -> sorted median = (25+25)/2.
    assert analytics["headline"]["median_lag_days"] == 25.0


def test_lag_histogram(analytics):
    hist = {row["bucket"]: row["count"] for row in analytics["lag_histogram"]}
    assert [row["bucket"] for row in analytics["lag_histogram"]] == [
        "0-30", "31-60", "61-90", "91-180", "180+",
    ]
    assert hist == {"0-30": 9, "31-60": 3, "61-90": 0, "91-180": 0, "180+": 0}


def test_monthly_contiguous_and_complete(analytics):
    monthly = analytics["monthly"]
    assert len(monthly) == 18  # 2024-01 .. 2025-06 inclusive
    months = [row["month"] for row in monthly]
    assert months[0] == "2024-01"
    assert months[-1] == "2025-06"
    # Every month present, strictly ascending, contiguous.
    expected = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]
    assert months == expected
    for row in monthly:
        assert row["total"] == row["dismissed"] + row["sustained"] + row["remanded"] + row["other"]
    # Spot checks: empty month, and the unknown-outcome case lands in "other".
    by_month = {row["month"]: row for row in monthly}
    assert by_month["2024-02"]["total"] == 0
    assert by_month["2025-05"] == {
        "month": "2025-05", "total": 2, "dismissed": 1, "sustained": 0, "remanded": 1, "other": 0,
    }
    assert by_month["2025-06"]["other"] == 1  # the unknown-outcome case


def test_yearly_rates_exclude_unknown(analytics):
    yearly = {row["year"]: row for row in analytics["yearly_outcomes"]}
    assert sorted(yearly) == [2024, 2025]
    assert yearly[2024]["total"] == 5
    assert yearly[2024]["rates"]["dismissed"] == round(4 / 5, 4)
    assert yearly[2024]["rates"]["sustained"] == round(1 / 5, 4)
    assert yearly[2024]["rates"]["remanded"] == 0.0
    # 2025 has 7 cases but only 6 with a known outcome.
    assert yearly[2025]["total"] == 7
    assert yearly[2025]["rates"]["dismissed"] == round(5 / 6, 4)
    assert yearly[2025]["rates"]["remanded"] == round(1 / 6, 4)
    assert yearly[2025]["rates"]["sustained"] == 0.0


def test_case_type_outcomes(analytics):
    by_type = {row["case_type"]: row for row in analytics["case_type_outcomes"]}
    assert by_type["appeal"]["total"] == 9
    assert by_type["appeal"]["outcomes"] == {"dismissed": 7, "sustained": 1, "remanded": 1}
    assert by_type["motion_reopen"]["outcomes"] == {"dismissed": 1}
    assert by_type["motion_combined"]["outcomes"] == {"dismissed": 1}
    assert by_type["unknown"]["outcomes"] == {"unknown": 1}


def test_criteria_canonical_order_and_labels(analytics):
    rows = analytics["criteria"]
    assert [r["key"] for r in rows] == [
        "awards", "membership", "published_material", "judging", "original_contributions",
        "scholarly_articles", "artistic_exhibitions", "leading_critical_role",
        "high_salary", "commercial_success",
    ]
    by_key = {r["key"]: r for r in rows}
    assert by_key["awards"]["label"] == "Awards & prizes"
    assert by_key["awards"]["cfr"] == "204.5(h)(3)(i)"
    assert by_key["commercial_success"]["cfr"] == "204.5(h)(3)(x)"


def test_criteria_judging(analytics):
    judging = next(r for r in analytics["criteria"] if r["key"] == "judging")
    assert judging["discussed"] == 8
    assert judging["met"] == 8
    assert judging["not_met"] == 0
    assert judging["met_rate"] == 1.0


def test_criteria_awards(analytics):
    awards = next(r for r in analytics["criteria"] if r["key"] == "awards")
    assert awards["discussed"] == 3
    assert awards["met"] == 1
    assert awards["not_met"] == 2
    assert awards["met_rate"] == round(1 / 3, 4)


def test_criteria_met_rate_null_when_no_rulings(analytics):
    # high_salary: one discussed-but-unresolved (met null) and one not met.
    high_salary = next(r for r in analytics["criteria"] if r["key"] == "high_salary")
    assert high_salary["met"] == 0
    assert high_salary["not_met"] == 1
    assert high_salary["met_rate"] == 0.0
    # membership was never counted as met -> in-favorable denominator is 0 -> None.
    membership = next(r for r in analytics["criteria"] if r["key"] == "membership")
    assert membership["met_rate_in_favorable"] is None


def test_criteria_met_distribution(analytics):
    dist = analytics["criteria_met_distribution"]
    assert [row["met_count"] for row in dist] == sorted(row["met_count"] for row in dist)
    by_count = {row["met_count"]: row for row in dist}
    assert by_count[0]["cases"] == 3
    assert by_count[2]["cases"] == 7
    assert by_count[2]["favorable"] == 1  # the remand had 2 criteria met
    # Rows with met_count >= 3 sum to exactly 2 cases (one 3, one 4).
    assert sum(row["cases"] for row in dist if row["met_count"] >= 3) == 2
    assert by_count[4]["favorable"] == 1  # the sustained case


def test_final_merits(analytics):
    assert analytics["final_merits"] == {
        "reached": 2, "met": 1, "not_met": 1, "not_reached": 10,
    }


def test_cooccurrence_favorable(analytics):
    pairs = analytics["cooccurrence_favorable"]
    # Sustained case met 4 criteria (6 pairs) + remand met 2 (1 pair).
    assert len(pairs) == 7
    assert all(p["count"] > 0 for p in pairs)
    as_sets = {frozenset((p["a"], p["b"])) for p in pairs}
    assert frozenset(("judging", "scholarly_articles")) in as_sets
    assert frozenset(("awards", "published_material")) in as_sets
    # Deterministic: sorted by count desc first.
    counts = [p["count"] for p in pairs]
    assert counts == sorted(counts, reverse=True)


def test_takeaways(analytics):
    takeaways = analytics["takeaways"]
    assert 4 <= len(takeaways) <= 6
    assert all(isinstance(t, str) and t.strip() for t in takeaways)


def test_qa(analytics):
    qa = analytics["qa"]
    assert qa["outcome_unknown_rate"] == round(1 / 12, 4)
    assert qa["extraction_error_count"] == 1  # the flagged no_order_line case
    assert qa["low_confidence_count"] == 1


def test_rounding_to_four_decimals(analytics):
    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, float):
            assert round(node, 4) == node

    walk(analytics)


def test_deterministic(cases):
    first = compute_analytics(cases, GENERATED_AT)
    second = compute_analytics(list(reversed(cases)), GENERATED_AT)
    # Aggregates must not depend on input order.
    assert first == second


def test_empty_input():
    result = compute_analytics([], GENERATED_AT)
    assert result["coverage"] == {
        "start": None, "end": None, "total_cases": 0, "unknown_outcome": 0,
    }
    assert result["headline"]["sustain_rate"] is None
    assert result["monthly"] == []
    assert result["qa"]["outcome_unknown_rate"] == 0.0
