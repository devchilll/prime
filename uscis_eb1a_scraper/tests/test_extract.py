"""Tests for uscis_eb1a_scraper.extract against the four decision fixtures."""

import json
from pathlib import Path

import pypdf
import pytest

from uscis_eb1a_scraper.extract import (
    CRITERIA,
    build_qa_report,
    pdf_to_text,
    text_to_case,
)

FIXTURES = Path(__file__).parent / "fixtures"

TOP_LEVEL_KEYS = {
    "id", "url", "filename", "category", "decision_date", "listing_month",
    "sha256", "pages", "text_chars", "extraction", "in_re_number", "form",
    "case_type", "outcome", "outcome_confidence", "service_center", "criteria",
    "criteria_met_count", "final_merits", "flags",
}

ALL_CRITERIA = [
    "awards", "membership", "published_material", "judging",
    "original_contributions", "scholarly_articles", "artistic_exhibitions",
    "leading_critical_role", "high_salary", "commercial_success",
]


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _meta(**overrides) -> dict:
    meta = {
        "id": "MAY282025_01B2203",
        "url": "https://www.uscis.gov/err/MAY282025_01B2203.pdf",
        "filename": "MAY282025_01B2203.pdf",
        "category": "eb1a",
        "decision_date": "2025-05-28",
        "listing_month": "2025-05",
        "sha256": "0" * 64,
        "pages": 8,
        "extracted_at": "2026-07-30T00:00:00Z",
    }
    meta.update(overrides)
    return meta


def _claimed_set(case: dict) -> set:
    return {k for k, v in case["criteria"].items() if v["claimed"]}


def _met_map(case: dict) -> dict:
    return {k: v["met"] for k, v in case["criteria"].items()}


# ---------------------------------------------------------------------------
# CRITERIA constant
# ---------------------------------------------------------------------------

def test_criteria_constant():
    assert list(CRITERIA) == ALL_CRITERIA
    for key, info in CRITERIA.items():
        assert info["label"]
        assert info["cite"].startswith("8 C.F.R.")
    assert "(i)" in CRITERIA["awards"]["cite"]
    assert "(x)" in CRITERIA["commercial_success"]["cite"]
    assert CRITERIA["published_material"]["label"] == "Published material about the person"


# ---------------------------------------------------------------------------
# Fixture: dismissed appeal
# ---------------------------------------------------------------------------

def test_dismissed_fixture():
    case = text_to_case(_fixture_text("decision_dismissed.txt"), _meta())
    assert set(case) == TOP_LEVEL_KEYS
    assert case["in_re_number"] == "32456789"
    assert case["form"] == "I-140"
    assert case["service_center"] == "Texas"
    assert case["case_type"] == "appeal"
    assert case["outcome"] == "dismissed"
    assert case["outcome_confidence"] == "high"
    assert case["decision_date"] == "2025-05-28"
    assert case["flags"] == []  # text date MAY 28, 2025 agrees with meta

    assert _claimed_set(case) == {
        "awards", "judging", "original_contributions",
        "scholarly_articles", "leading_critical_role",
    }
    met = _met_map(case)
    assert met["judging"] is True
    assert met["scholarly_articles"] is True
    assert met["awards"] is False
    assert met["original_contributions"] is False
    assert met["leading_critical_role"] is False
    for key in ("membership", "published_material", "artistic_exhibitions",
                "high_salary", "commercial_success"):
        assert case["criteria"][key]["discussed"] is False
        assert case["criteria"][key]["claimed"] is False
        assert met[key] is None
    assert case["criteria_met_count"] == 2
    assert case["final_merits"] == {"reached": False, "outcome": None}

    assert case["extraction"]["status"] == "ok"
    assert case["extraction"]["extractor"].startswith("pypdf-")
    assert case["extraction"]["extracted_at"] == "2026-07-30T00:00:00Z"
    assert case["text_chars"] > 2000
    assert case["pages"] == 8
    assert case["sha256"] == "0" * 64


def test_dismissed_date_mismatch_flag():
    case = text_to_case(_fixture_text("decision_dismissed.txt"),
                        _meta(decision_date="2025-05-01"))
    assert "date_mismatch_text_vs_filename" in case["flags"]
    # Filename-derived date stays authoritative.
    assert case["decision_date"] == "2025-05-01"


# ---------------------------------------------------------------------------
# Fixture: sustained appeal
# ---------------------------------------------------------------------------

def test_sustained_fixture():
    case = text_to_case(_fixture_text("decision_sustained.txt"),
                        _meta(decision_date="2025-05-27"))
    assert case["in_re_number"] == "28773401"
    assert case["service_center"] == "Nebraska"
    assert case["case_type"] == "appeal"
    assert case["outcome"] == "sustained"
    assert case["outcome_confidence"] == "high"
    assert case["flags"] == []

    assert _claimed_set(case) == {
        "awards", "published_material", "artistic_exhibitions",
        "leading_critical_role",
    }
    met = _met_map(case)
    for key in ("awards", "published_material", "artistic_exhibitions",
                "leading_critical_role"):
        assert met[key] is True
    for key in ("membership", "judging", "original_contributions",
                "scholarly_articles", "high_salary", "commercial_success"):
        assert met[key] is None
    assert case["criteria_met_count"] == 4
    assert case["final_merits"] == {"reached": True, "outcome": "met"}


# ---------------------------------------------------------------------------
# Fixture: combined motion, dismissed
# ---------------------------------------------------------------------------

def test_motion_fixture():
    case = text_to_case(_fixture_text("decision_motion.txt"),
                        _meta(decision_date="2025-05-23"))
    assert case["in_re_number"] == "30918822"
    assert case["case_type"] == "motion_combined"
    assert case["outcome"] == "dismissed"  # both ORDER lines dismissed
    assert case["outcome_confidence"] == "high"
    assert case["form"] == "I-140"
    assert case["service_center"] is None
    # The motion fixture is a short decision (< 2000 chars), so the
    # low-text heuristic flag fires; no other flags are expected.
    assert case["flags"] == ["low_text"]

    crit = case["criteria"]["original_contributions"]
    assert crit["discussed"] is True
    assert crit["claimed"] is True
    assert crit["met"] is False
    assert case["criteria_met_count"] == 0


# ---------------------------------------------------------------------------
# Fixture: remand (decision withdrawn + matter remanded -> remanded)
# ---------------------------------------------------------------------------

def test_remand_fixture():
    case = text_to_case(_fixture_text("decision_remand.txt"),
                        _meta(decision_date="2025-05-21"))
    assert case["in_re_number"] == "27665130"
    assert case["service_center"] == "California"
    assert case["case_type"] == "appeal"
    assert case["outcome"] == "remanded"  # NOT withdrawn
    assert case["outcome_confidence"] == "high"

    met = _met_map(case)
    assert met["judging"] is True
    assert met["scholarly_articles"] is True
    assert met["high_salary"] is None
    assert case["criteria"]["high_salary"]["discussed"] is True
    assert case["criteria"]["high_salary"]["claimed"] is True
    assert case["criteria_met_count"] == 2
    # No final merits determination was conducted (remand instructs the
    # Director to conduct one if warranted).
    assert case["final_merits"] == {"reached": False, "outcome": None}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_text_yields_unknowns_and_flags():
    case = text_to_case("", _meta(decision_date=None, pages=None))
    assert case["in_re_number"] is None
    assert case["form"] is None
    assert case["service_center"] is None
    assert case["decision_date"] is None
    assert case["case_type"] == "unknown"
    assert case["outcome"] == "unknown"
    assert case["outcome_confidence"] == "low"
    for flag in ("case_type_unknown", "no_order_line", "low_text"):
        assert flag in case["flags"]
    for key in ALL_CRITERIA:
        assert case["criteria"][key] == {"discussed": False, "claimed": False,
                                         "met": None}
    assert case["criteria_met_count"] == 0
    assert case["final_merits"] == {"reached": False, "outcome": None}
    assert case["text_chars"] == 0


def test_range_cite_does_not_mark_criteria_discussed():
    text = ("The Director denied the petition, concluding that the record did "
            "not establish that the Petitioner meets at least three of the ten "
            "initial evidentiary criteria at 8 C.F.R. § 204.5(h)(3)(i)-(x). "
            "Nothing else is discussed here.")
    case = text_to_case(text, _meta(decision_date=None))
    for key in ALL_CRITERIA:
        assert case["criteria"][key]["discussed"] is False, key
        assert case["criteria"][key]["claimed"] is False, key


def test_specific_cite_still_marks_discussed():
    text = "The Petitioner relies on 8 C.F.R. § 204.5(h)(3)(iv) only."
    case = text_to_case(text, _meta(decision_date=None))
    assert case["criteria"]["judging"]["discussed"] is True
    assert case["criteria"]["awards"]["discussed"] is False


# ---------------------------------------------------------------------------
# pdf_to_text
# ---------------------------------------------------------------------------

def test_pdf_to_text_blank_pages(tmp_path):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    pdf_path = tmp_path / "blank.pdf"
    with open(pdf_path, "wb") as fh:
        writer.write(fh)

    text, pages = pdf_to_text(pdf_path)
    assert isinstance(text, str)
    assert isinstance(pages, int)
    assert pages == 3
    assert "\n\n\n" not in text  # normalization collapses newline runs


def test_pdf_to_text_encrypted_raises(tmp_path):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret-password")
    pdf_path = tmp_path / "locked.pdf"
    with open(pdf_path, "wb") as fh:
        writer.write(fh)

    with pytest.raises(ValueError):
        pdf_to_text(pdf_path)


def test_pdf_to_text_unreadable_raises(tmp_path):
    pdf_path = tmp_path / "garbage.pdf"
    pdf_path.write_bytes(b"this is not a pdf at all")
    with pytest.raises(ValueError):
        pdf_to_text(pdf_path)


# ---------------------------------------------------------------------------
# build_qa_report
# ---------------------------------------------------------------------------

def _load_sample_cases() -> list[dict]:
    lines = (FIXTURES / "cases_sample.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_build_qa_report_shape_and_determinism():
    cases = _load_sample_cases()
    report = build_qa_report(cases)
    for key in ("field_null_rates", "outcome_distribution", "samples",
                "flagged_cases"):
        assert key in report

    assert sum(report["outcome_distribution"].values()) == len(cases)
    for field, rate in report["field_null_rates"].items():
        assert 0.0 <= rate <= 1.0, field

    all_ids = {c["id"] for c in cases}
    for outcome, sampled in report["samples"].items():
        assert outcome in report["outcome_distribution"]
        assert len(sampled) <= 5
        assert set(sampled) <= all_ids

    # Deterministic under the same seed; input order must not matter more
    # than the seed does.
    again = build_qa_report(list(reversed(cases)))
    assert again["samples"] == report["samples"]
    assert again["outcome_distribution"] == report["outcome_distribution"]

    assert isinstance(report["flagged_cases"], list)
    for entry in report["flagged_cases"]:
        assert entry["flags"]
        assert entry["id"] in all_ids


def test_build_qa_report_samples_per_class_cap():
    cases = _load_sample_cases()
    report = build_qa_report(cases, samples_per_class=1)
    for sampled in report["samples"].values():
        assert len(sampled) == 1


def test_build_qa_report_empty():
    report = build_qa_report([])
    assert report["outcome_distribution"] == {}
    assert report["samples"] == {}
    assert report["flagged_cases"] == []
