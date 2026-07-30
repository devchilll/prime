"""Pure-function extraction of per-case metadata from USCIS AAO EB-1A decision text.

Public API (frozen, imported by pipeline.py):
    pdf_to_text(pdf_path) -> tuple[str, int]
    text_to_case(text, meta) -> dict
    build_qa_report(cases, sample_seed=42, samples_per_class=5) -> dict
    CRITERIA  (ordered mapping of the ten 8 C.F.R. 204.5(h)(3) criteria)
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pypdf

# ---------------------------------------------------------------------------
# The ten EB-1A evidentiary criteria, in regulatory order.
# ---------------------------------------------------------------------------

CRITERIA: dict[str, dict[str, str]] = {
    "awards": {
        "label": "Nationally/internationally recognized prizes or awards",
        "cite": "8 C.F.R. § 204.5(h)(3)(i)",
        "roman": "i",
    },
    "membership": {
        "label": "Membership in associations requiring outstanding achievement",
        "cite": "8 C.F.R. § 204.5(h)(3)(ii)",
        "roman": "ii",
    },
    "published_material": {
        "label": "Published material about the person",
        "cite": "8 C.F.R. § 204.5(h)(3)(iii)",
        "roman": "iii",
    },
    "judging": {
        "label": "Judging the work of others in the field",
        "cite": "8 C.F.R. § 204.5(h)(3)(iv)",
        "roman": "iv",
    },
    "original_contributions": {
        "label": "Original contributions of major significance",
        "cite": "8 C.F.R. § 204.5(h)(3)(v)",
        "roman": "v",
    },
    "scholarly_articles": {
        "label": "Authorship of scholarly articles",
        "cite": "8 C.F.R. § 204.5(h)(3)(vi)",
        "roman": "vi",
    },
    "artistic_exhibitions": {
        "label": "Display of work at artistic exhibitions or showcases",
        "cite": "8 C.F.R. § 204.5(h)(3)(vii)",
        "roman": "vii",
    },
    "leading_critical_role": {
        "label": "Leading or critical role for distinguished organizations",
        "cite": "8 C.F.R. § 204.5(h)(3)(viii)",
        "roman": "viii",
    },
    "high_salary": {
        "label": "High salary or significantly high remuneration",
        "cite": "8 C.F.R. § 204.5(h)(3)(ix)",
        "roman": "ix",
    },
    "commercial_success": {
        "label": "Commercial success in the performing arts",
        "cite": "8 C.F.R. § 204.5(h)(3)(x)",
        "roman": "x",
    },
}

# Phrase alternates per criterion (case-insensitive; \s+ tolerates line wraps).
_PHRASES: dict[str, list[str]] = {
    "awards": [r"prizes\s+or\s+awards"],
    "membership": [r"membership\s+in\s+associations"],
    "published_material": [r"published\s+material\s+about"],
    "judging": [r"as\s+a\s+judge\s+of\s+the\s+work\s+of\s+others"],
    # Allow the regulatory adjective list ("original scientific, scholarly, ...
    # contributions of major significance") and line wraps between the words.
    "original_contributions": [r"original[\s\S]{0,80}?contributions\s+of\s+major\s+significance"],
    "scholarly_articles": [r"authorship\s+of\s+scholarly\s+articles"],
    "artistic_exhibitions": [r"artistic\s+exhibitions\s+or\s+showcases"],
    "leading_critical_role": [r"leading\s+or\s+critical\s+role"],
    "high_salary": [r"high\s+salary", r"significantly\s+high\s+remuneration"],
    "commercial_success": [r"commercial\s+success"],
}


def _criterion_regex(key: str) -> re.Pattern:
    roman = CRITERIA[key]["roman"]
    # The trailing negative lookahead keeps the boilerplate range cite
    # "204.5(h)(3)(i)-(x)" from counting as a mention of criterion (i).
    cite = rf"204\.5\(h\)\(3\)\({roman}\)(?!\s*[-–—])"
    parts = [cite] + _PHRASES[key]
    return re.compile("|".join(f"(?:{p})" for p in parts), re.IGNORECASE)


_CRITERION_RES: dict[str, re.Pattern] = {k: _criterion_regex(k) for k in CRITERIA}

# ---------------------------------------------------------------------------
# Verdict patterns (met tri-state)
# ---------------------------------------------------------------------------

_POSITIVE_RES = [
    # Subject must be "the Petitioner" or "this evidence" so the pattern does
    # not fire inside negative sentences like "has not established that he
    # meets this criterion".
    re.compile(
        r"Petitioner\s+(?:meets|met|satisfies|satisfied|has\s+satisfied)\s+this\s+criterion",
        re.IGNORECASE,
    ),
    re.compile(r"evidence\s+satisfies\s+this\s+criterion", re.IGNORECASE),
]

# "we agree" following a Director-found-met statement (both must appear in the
# same paragraph to count as a positive finding).
_DIRECTOR_MET_RE = re.compile(
    r"Director\s+(?:found|concluded|determined)\s+that\s+the\s+Petitioner\s+"
    r"(?:meets|met|satisfies|satisfied)",
    re.IGNORECASE,
)
_WE_AGREE_RE = re.compile(r"we\s+agree", re.IGNORECASE)

_NEGATIVE_RES = [
    re.compile(
        r"has\s+not\s+(?:established|demonstrated|shown)\s+that\s+"
        r"(?:he|she|they|it|the\s+Petitioner)\s+meets?\s+this\s+criterion",
        re.IGNORECASE,
    ),
    re.compile(r"does\s+not\s+meet\s+this\s+criterion", re.IGNORECASE),
    re.compile(
        r"ha(?:s|ve)\s+not\s+established\s+that\s+(?:he|she|they|it|the\s+Petitioner)\s+"
        r"(?:meets?|satisf(?:y|ies|ied))\s+this\s+criterion",
        re.IGNORECASE,
    ),
]

# Summary paragraph: "has satisfied N of the initial evidentiary criteria,
# relating to X and Y" confirms met for the named criteria.  (The claims
# enumeration "claims to have satisfied five criteria, relating to ..." does
# NOT match because it lacks "of the initial evidentiary criteria".)
_SUMMARY_RE = re.compile(
    r"satisfied\s+\w+\s+of\s+the\s+initial\s+evidentiary\s+criteria,?\s+"
    r"relating\s+to\s+([^.]+)",
    re.IGNORECASE,
)

_SUMMARY_NAME_MAP: list[tuple[str, str]] = [
    ("judg", "judging"),
    ("scholarly", "scholarly_articles"),
    ("prize", "awards"),
    ("award", "awards"),
    ("membership", "membership"),
    ("published material", "published_material"),
    ("original contribution", "original_contributions"),
    ("exhibition", "artistic_exhibitions"),
    ("showcase", "artistic_exhibitions"),
    ("critical role", "leading_critical_role"),
    ("leading", "leading_critical_role"),
    ("salary", "high_salary"),
    ("remuneration", "high_salary"),
    ("commercial", "commercial_success"),
]

# ---------------------------------------------------------------------------
# Other field patterns
# ---------------------------------------------------------------------------

_IN_RE_RE = re.compile(r"In\s+Re:\s*([0-9][0-9,\-\s]*)", re.IGNORECASE)
_FORM_RE = re.compile(r"Form\s+(I-\d+)", re.IGNORECASE)
_DATE_RE = re.compile(r"Date:\s*([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s+(\d{4})", re.IGNORECASE)
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_ORDER_RE = re.compile(r"(?:FURTHER\s+)?ORDER:\s*(.{0,200}?)(?:\n|$)", re.IGNORECASE)
_NARRATIVE_RE = re.compile(r"we\s+will\s+(dismiss|sustain|remand)", re.IGNORECASE)
_NARRATIVE_DISMISS_RE = re.compile(r"the\s+appeal\s+will\s+be\s+dismissed", re.IGNORECASE)

_SERVICE_CENTER_RES = [
    re.compile(r"(?:Appeal|Motion)\s+of\s+(?:the\s+)?(\w+)\s+Service\s+Center\s+Decision", re.IGNORECASE),
    re.compile(r"Director\s+of\s+the\s+(\w+)\s+Service\s+Center", re.IGNORECASE),
]
_KNOWN_CENTERS = {"texas": "Texas", "nebraska": "Nebraska", "california": "California",
                  "vermont": "Vermont", "potomac": "Potomac"}

_FM_MENTION_RE = re.compile(r"final\s+merits\s+determination", re.IGNORECASE)
_FM_NOT_REACHED_RE = re.compile(
    r"need\s+not\s+(?:provide|conduct|reach|make)[^.]{0,150}?final\s+merits", re.IGNORECASE
)
_FM_REACHED_RES = [
    re.compile(r"we\s+turn\s+to\s+a\s+final\s+merits\s+determination", re.IGNORECASE),
    re.compile(r"we\s+(?:will\s+|have\s+|now\s+)?conduct(?:ed)?\s+a\s+final\s+merits\s+determination",
               re.IGNORECASE),
    re.compile(r"final\s+merits\s+determination,\s+considering\s+the\s+totality", re.IGNORECASE),
]
_FM_MET_RES = [
    re.compile(r"has\s+sustained\s+national[\s\S]{0,60}?acclaim", re.IGNORECASE),
    re.compile(r"among\s+the\s+small\s+percentage\s+at\s+the\s+very\s+top", re.IGNORECASE),
]
_FM_NOT_MET_RES = [
    re.compile(r"does\s+not\s+support\s+a\s+finding", re.IGNORECASE),
    re.compile(r"has\s+not\s+established\s+the\s+(?:sustained\s+)?acclaim", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# pdf_to_text
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Strip trailing spaces per line, collapse 3+ newlines to 2."""
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text)


def pdf_to_text(pdf_path) -> tuple[str, int]:
    """Extract normalized text from a PDF. Returns (text, page_count).

    Raises ValueError on unreadable or encrypted (password-protected) files.
    """
    path = Path(pdf_path)
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            try:
                decrypt_result = reader.decrypt("")
            except Exception as exc:  # pypdf raises on unsupported crypt filters
                raise ValueError(f"Encrypted PDF could not be decrypted: {path}") from exc
            if decrypt_result == pypdf.PasswordType.NOT_DECRYPTED:
                raise ValueError(f"Encrypted PDF (password required): {path}")
        page_texts = [page.extract_text() or "" for page in reader.pages]
        page_count = len(reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Unreadable PDF {path}: {exc}") from exc
    return _normalize("\n".join(page_texts)), page_count


# ---------------------------------------------------------------------------
# text_to_case helpers
# ---------------------------------------------------------------------------

def _parse_text_date(text: str) -> str | None:
    """Parse 'Date: MAY 28, 2025' from the first 1500 chars -> 'YYYY-MM-DD'."""
    m = _DATE_RE.search(text[:1500])
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].upper())
    if month is None:
        return None
    try:
        day, year = int(m.group(2)), int(m.group(3))
    except ValueError:
        return None
    if not (1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _extract_case_type(text: str) -> str | None:
    head = text[:3000]
    reopen = re.search(r"motion\s+to\s+reopen", head, re.IGNORECASE)
    reconsider = re.search(r"motion\s+to\s+reconsider", head, re.IGNORECASE)
    combined = re.search(r"combined\s+motion", head, re.IGNORECASE)
    if combined or (reopen and reconsider):
        return "motion_combined"
    if reopen:
        return "motion_reopen"
    if reconsider:
        return "motion_reconsider"
    if re.search(r"on\s+appeal|Appeal\s+of\b", head, re.IGNORECASE):
        return "appeal"
    return None


def _classify_order_text(order_text: str) -> str | None:
    t = order_text.lower()
    if "sustain" in t:
        return "sustained"
    # Check remand before withdrawn: "The Director's decision is withdrawn.
    # The matter is remanded ..." must map to remanded, not withdrawn.
    if "remand" in t:
        return "remanded"
    if "dismissed in part" in t or "dismiss in part" in t:
        return "dismissed_in_part"
    if "dismiss" in t:
        return "dismissed"
    if "reject" in t:
        return "rejected"
    if "withdraw" in t:
        return "withdrawn"
    return None


_OUTCOME_PRECEDENCE = ["sustained", "remanded", "dismissed_in_part",
                       "dismissed", "rejected", "withdrawn"]


def _extract_outcome(text: str) -> tuple[str, str, list[str]]:
    """Return (outcome, confidence, extra_flags)."""
    tail = text[-2500:]
    order_texts = _ORDER_RE.findall(tail)
    labels = []
    for order_text in order_texts:
        label = _classify_order_text(order_text)
        if label == "withdrawn":
            # "The Director's decision is withdrawn" is remand context, not a
            # case withdrawal, whenever a remand appears anywhere in the tail.
            if (re.search(r"decision\s+is\s+withdrawn", order_text, re.IGNORECASE)
                    and re.search(r"remand", tail, re.IGNORECASE)):
                label = "remanded"
        if label:
            labels.append(label)
    if labels:
        for outcome in _OUTCOME_PRECEDENCE:
            if outcome in labels:
                return outcome, "high", []
    # Narrative fallback in the last 4000 chars.
    tail4 = text[-4000:]
    m = _NARRATIVE_RE.search(tail4)
    if m:
        return {"dismiss": "dismissed", "sustain": "sustained",
                "remand": "remanded"}[m.group(1).lower()], "medium", []
    if _NARRATIVE_DISMISS_RE.search(tail4):
        return "dismissed", "medium", []
    return "unknown", "low", ["no_order_line"]


def _extract_service_center(text: str) -> str | None:
    for pattern in _SERVICE_CENTER_RES:
        m = pattern.search(text)
        if m:
            return _KNOWN_CENTERS.get(m.group(1).lower())
    return None


def _extract_criteria(text: str) -> dict[str, dict]:
    """Per-criterion discussed/claimed/met via paragraph-scoped verdicts.

    Verdict sentences ("The Petitioner meets this criterion") usually sit in an
    analysis paragraph that does not repeat the criterion's cite or phrase, so
    verdicts in a paragraph with no criterion mention attach to the criteria
    mentioned in the most recent mentioning paragraph (the section heading).
    """
    result = {k: {"discussed": bool(_CRITERION_RES[k].search(text)),
                  "claimed": False, "met": None} for k in CRITERIA}
    for k in CRITERIA:
        # A criterion counts as claimed when it is genuinely discussed (the
        # range cite "(i)-(x)" is already excluded from `discussed`).
        result[k]["claimed"] = result[k]["discussed"]

    positive = {k: False for k in CRITERIA}
    negative = {k: False for k in CRITERIA}
    current: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        mentioned = [k for k in CRITERIA if _CRITERION_RES[k].search(para)]
        has_pos = (any(p.search(para) for p in _POSITIVE_RES)
                   or (_DIRECTOR_MET_RE.search(para) and _WE_AGREE_RE.search(para)))
        has_neg = any(n.search(para) for n in _NEGATIVE_RES)
        targets = mentioned if mentioned else current
        if has_pos:
            for k in targets:
                positive[k] = True
        if has_neg:
            for k in targets:
                negative[k] = True
        if mentioned:
            current = mentioned

    # Summary confirmation: "satisfied N of the initial evidentiary criteria,
    # relating to judging and scholarly articles".
    m = _SUMMARY_RE.search(text)
    if m:
        named = m.group(1).lower()
        for needle, key in _SUMMARY_NAME_MAP:
            if needle in named:
                positive[key] = True

    for k in CRITERIA:
        if positive[k] and not negative[k]:
            result[k]["met"] = True
        elif negative[k] and not positive[k]:
            result[k]["met"] = False
        else:
            result[k]["met"] = None
    return result


def _extract_final_merits(text: str) -> dict:
    reached = False
    outcome = None
    if _FM_MENTION_RE.search(text):
        if _FM_NOT_REACHED_RE.search(text):
            reached = False
        elif any(p.search(text) for p in _FM_REACHED_RES):
            reached = True
    if reached:
        met = any(p.search(text) for p in _FM_MET_RES)
        not_met = any(p.search(text) for p in _FM_NOT_MET_RES)
        if met and not not_met:
            outcome = "met"
        elif not_met and not met:
            outcome = "not_met"
    return {"reached": reached, "outcome": outcome}


# ---------------------------------------------------------------------------
# text_to_case
# ---------------------------------------------------------------------------

def text_to_case(text: str, meta: dict) -> dict:
    """Build the frozen case record from normalized decision text + metadata."""
    text = _normalize(text or "")
    flags: list[str] = []

    # In Re number (first 1500 chars, separators stripped).
    in_re_number = None
    m = _IN_RE_RE.search(text[:1500])
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        in_re_number = digits or None

    # Form (first 3000 chars).
    form = None
    m = _FORM_RE.search(text[:3000])
    if m:
        form = m.group(1).upper()

    # Decision date: filename-derived meta is authoritative; text date used
    # for cross-checking (and as fallback when meta has none).
    text_date = _parse_text_date(text)
    decision_date = meta.get("decision_date")
    if decision_date and text_date and decision_date != text_date:
        flags.append("date_mismatch_text_vs_filename")
    elif not decision_date and text_date:
        decision_date = text_date

    case_type = _extract_case_type(text)
    if case_type is None:
        case_type = "unknown"
        flags.append("case_type_unknown")

    outcome, outcome_confidence, outcome_flags = _extract_outcome(text)
    flags.extend(outcome_flags)

    criteria = _extract_criteria(text)
    criteria_met_count = sum(1 for c in criteria.values() if c["met"] is True)
    final_merits = _extract_final_merits(text)
    service_center = _extract_service_center(text)

    if len(text) < 2000:
        flags.append("low_text")

    return {
        "id": meta.get("id"),
        "url": meta.get("url"),
        "filename": meta.get("filename"),
        "category": meta.get("category"),
        "decision_date": decision_date,
        "listing_month": meta.get("listing_month"),
        "sha256": meta.get("sha256"),
        "pages": meta.get("pages"),
        "text_chars": len(text),
        "extraction": {
            "status": "ok",
            "extractor": f"pypdf-{pypdf.__version__}",
            "extracted_at": meta.get("extracted_at"),
        },
        "in_re_number": in_re_number,
        "form": form,
        "case_type": case_type,
        "outcome": outcome,
        "outcome_confidence": outcome_confidence,
        "service_center": service_center,
        "criteria": criteria,
        "criteria_met_count": criteria_met_count,
        "final_merits": final_merits,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# build_qa_report
# ---------------------------------------------------------------------------

_QA_NULLABLE_FIELDS = ["in_re_number", "form", "decision_date", "listing_month",
                       "service_center", "pages", "sha256"]


def build_qa_report(cases: list[dict], sample_seed: int = 42,
                    samples_per_class: int = 5) -> dict:
    """QA summary over extracted case records.

    Returns {"total_cases", "field_null_rates", "outcome_distribution",
    "samples", "flagged_cases"}; `samples` is a deterministic per-outcome
    random sample of case ids under `sample_seed`.
    """
    total = len(cases)
    field_null_rates = {
        field: (round(sum(1 for c in cases if c.get(field) is None) / total, 4)
                if total else 0.0)
        for field in _QA_NULLABLE_FIELDS
    }

    outcome_distribution: dict[str, int] = {}
    by_outcome: dict[str, list[str]] = {}
    for c in cases:
        outcome = c.get("outcome") or "unknown"
        outcome_distribution[outcome] = outcome_distribution.get(outcome, 0) + 1
        by_outcome.setdefault(outcome, []).append(str(c.get("id")))

    rng = random.Random(sample_seed)
    samples: dict[str, list[str]] = {}
    for outcome in sorted(by_outcome):
        ids = sorted(by_outcome[outcome])
        if len(ids) <= samples_per_class:
            samples[outcome] = ids
        else:
            samples[outcome] = sorted(rng.sample(ids, samples_per_class))

    flagged_cases = [{"id": c.get("id"), "flags": list(c.get("flags") or [])}
                     for c in cases if c.get("flags")]

    return {
        "total_cases": total,
        "field_null_rates": field_null_rates,
        "outcome_distribution": outcome_distribution,
        "samples": samples,
        "flagged_cases": flagged_cases,
    }
