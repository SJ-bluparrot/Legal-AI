"""
utils.py — Shared utilities for the Legal AI pipeline
-------------------------------------------------------
This file exists to break the circular import between app.py and
complaint_drafter.py.

The problem before this file existed:
    app.py            defines normalize_case_fields()
    complaint_drafter imports normalize_case_fields from app.py
    complaint_router  imports from complaint_drafter
    app.py            imports complaint_router
    → circular import: app → complaint_router → complaint_drafter → app

The solution:
    Move normalize_case_fields here (utils.py).
    Both app.py and complaint_drafter.py import from utils.py.
    utils.py imports nothing from the project — zero circular risk.

Usage:
    from utils import normalize_case_fields

    normalized = normalize_case_fields(provided_fields, required_fields)
    # → { "plaintiff_name": "John Doe", "defendant_name": "[UNKNOWN]", ... }
"""

import logging

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Source priority table
# ──────────────────────────────────────────────
# Every extracted field carries a "source" tag that records HOW it was found.
# Higher number = more trustworthy. Used by merge_provided_fields() in
# entity_extractor.py to decide which value wins when the same field is
# extracted twice from different turns of attorney input.
#
# Priority rules:
#   llm (1)  < regex (2) < human (3) < corrected (4)
#
# Example:
#   Turn 1: Haiku extracts   plaintiff_name = "John Doe slipped"  (source=llm,   priority=1)
#   Turn 2: regex extracts   plaintiff_name = "John Doe"          (source=regex,  priority=2)
#   → regex wins (2 > 1) even though "John Doe" is shorter.      BUG FIXED ✅
#
#   Turn 3: attorney types   plaintiff_name = "John M. Doe"       (source=human,  priority=3)
#   → human wins (3 > 2) — attorney is always authoritative.     ✅
# ──────────────────────────────────────────────
FIELD_SOURCE_PRIORITY: dict[str, int] = {
    "llm":       1,  # LLM output — useful but may bleed surrounding context
    "regex":     2,  # Pattern-matched — high precision for dates, names, amounts
    "human":     3,  # Attorney provided directly via /provide endpoint
    "corrected": 4,  # Attorney explicitly corrected a prior AI-filled value
}


def normalize_case_fields(
    provided_fields: dict,
    required_fields: list[str],
) -> dict:
    """
    Prepare case fields for the complaint drafting prompt.

    Any required field that is missing or empty is replaced with the
    literal string "[UNKNOWN]". This ensures Claude always receives a
    complete set of fields and writes "[UNKNOWN]" into the complaint
    where data is absent — making gaps visible and easy for the attorney
    to fix — rather than hallucinating plausible-sounding invented values.

    Optional fields that are present are passed through as-is.
    Optional fields that are missing are omitted entirely so the prompt
    is not cluttered with "[UNKNOWN]" entries for fields never needed.

    Args:
        provided_fields : Current provided_fields from the case session.
        required_fields : List of required element IDs for this case type.

    Returns:
        A new dict — does NOT modify provided_fields in place.

    Example:
        provided  = {"plaintiff_name": "John Doe", "incident_date": "Jan 5"}
        required  = ["plaintiff_name", "defendant_name",
                     "incident_date",  "incident_location"]
        result    = normalize_case_fields(provided, required)
        # → {
        #     "plaintiff_name":    "John Doe",
        #     "defendant_name":    "[UNKNOWN]",   ← missing required
        #     "incident_date":     "Jan 5",
        #     "incident_location": "[UNKNOWN]",   ← missing required
        #   }
        # Optional fields not in required_fields are passed through if
        # present, and omitted if absent.

    Note on the duplicate-function problem this file solves:
        Previously this function existed in BOTH app.py and complaint_drafter.py.
        Any bug fix made to one copy had to be manually copied to the other —
        a maintenance trap. Now there is exactly ONE definition here, and both
        files import from utils.py.
    """
    required_set = set(required_fields)
    normalized   = {}

    # Required fields: substitute [UNKNOWN] for any that are missing or empty
    for field_id in required_fields:
        value = provided_fields.get(field_id, "")
        normalized[field_id] = value.strip() if value and value.strip() else "[UNKNOWN]"

    # Optional fields: pass through only if they carry a real value.
    # Skip internal metadata keys (e.g. "__sources__") — these are pipeline
    # bookkeeping and must never appear in the Claude drafting prompt.
    for field_id, value in provided_fields.items():
        if field_id.startswith("__"):
            continue   # skip internal metadata (source tracking, etc.)
        if field_id in required_set:
            continue   # already handled above
        if value and str(value).strip():
            normalized[field_id] = str(value).strip()

    unknown_count = sum(1 for v in normalized.values() if v == "[UNKNOWN]")
    if unknown_count:
        logger.info(
            f"normalize_case_fields: {unknown_count} required field(s) → [UNKNOWN] "
            f"out of {len(required_fields)} required"
        )

    return normalized


# ──────────────────────────────────────────────
# Court caption helpers (Day 8 upgrade)
# ──────────────────────────────────────────────

# Case types that belong in federal court vs. state court.
# Federal courts handle constitutional claims, civil rights, and federal statutes.
# All other case types default to state Superior Court.
_FEDERAL_CASE_TYPES = {
    "eminent_domain",      # Fifth Amendment Takings Clause
    "employment_dispute",  # Title VII / ADEA / ADA / FLSA are federal statutes
    "criminal_defense",    # Federal charges use federal district courts
}


def auto_detect_court(case_type: str) -> str:
    """
    Return the appropriate court name for a given case type.

    Federal cases use "UNITED STATES DISTRICT COURT".
    All other cases default to "SUPERIOR COURT OF THE STATE OF [STATE]"
    (the state placeholder is filled by build_court_caption using court_state).

    Args:
        case_type : The detected case type string (e.g. "personal_injury").

    Returns:
        A court name string — never None or empty.

    Example:
        auto_detect_court("employment_dispute") → "UNITED STATES DISTRICT COURT"
        auto_detect_court("personal_injury")    → "SUPERIOR COURT"
    """
    if case_type in _FEDERAL_CASE_TYPES:
        return "UNITED STATES DISTRICT COURT"
    return "SUPERIOR COURT"


def build_court_caption(fields: dict, case_type: str = "") -> str:
    """
    Build the formal court caption block for a legal complaint.

    Uses intake-provided court fields when available, and auto-detects
    the court name from case_type when court_name is not explicitly provided.

    Fields consumed (all optional — fall back to [UNKNOWN] or auto-detected values):
        court_name     : e.g. "United States District Court"
        court_district : e.g. "Central District"
        court_state    : e.g. "California"
        case_number    : e.g. "2:24-cv-01234" or left as [CASE NO. TO BE ASSIGNED]
        plaintiff_name : Full legal name of the plaintiff / petitioner
        defendant_name : Full legal name of the defendant / respondent

    Args:
        fields    : Normalized case fields dict (with [UNKNOWN] for missing values).
        case_type : The detected case type string — used for court auto-detection
                    when court_name is not provided.

    Returns:
        A multi-line string containing the full court caption, ready to prepend
        to the complaint body prompt.

    Example output:
        IN THE UNITED STATES DISTRICT COURT
        FOR THE CENTRAL DISTRICT OF CALIFORNIA

        JOHN DOE,
                Plaintiff,

        v.                                          Case No. [CASE NO. TO BE ASSIGNED]

        WALMART INC.,
                Defendant.
    """
    # ── Court name: use provided value, fall back to auto-detected ────────────
    court_name_raw = fields.get("court_name", "").strip()
    if not court_name_raw or court_name_raw == "[UNKNOWN]":
        court_name = auto_detect_court(case_type)
    else:
        court_name = court_name_raw.upper()

    # ── District and state line ───────────────────────────────────────────────
    district = fields.get("court_district", "").strip()
    state    = fields.get("court_state",    "").strip()

    # Strip trailing "district" from the value if present — attorneys often type
    # "Central District" but the caption template appends "DISTRICT OF" itself,
    # which would produce "CENTRAL DISTRICT DISTRICT OF CALIFORNIA" otherwise.
    district_clean = district.upper().removesuffix(" DISTRICT").strip()

    if district_clean and district_clean != "[UNKNOWN]" and state and state != "[UNKNOWN]":
        venue_line = f"FOR THE {district_clean} DISTRICT OF {state.upper()}"
    elif state and state != "[UNKNOWN]":
        venue_line = f"OF THE STATE OF {state.upper()}"
    else:
        venue_line = "FOR THE [DISTRICT] OF [STATE]"

    # ── Case number ───────────────────────────────────────────────────────────
    case_number = fields.get("case_number", "").strip()
    if not case_number or case_number == "[UNKNOWN]":
        case_number = "[CASE NO. TO BE ASSIGNED]"

    # ── Parties ───────────────────────────────────────────────────────────────
    # Sanitize party names: collapse embedded newlines/tabs so injected values
    # (e.g. "John Doe\n\nDROP TABLE") cannot break the caption structure.
    def _clean(v: str) -> str:
        return " ".join(str(v).split()).strip()

    plaintiff = _clean(fields.get("plaintiff_name", "[UNKNOWN]"))
    defendant = _clean(fields.get("defendant_name", "[UNKNOWN]"))

    # For family law, use "Petitioner" / "Respondent" instead of Plaintiff / Defendant
    if case_type == "family_law":
        plaintiff_role = "Petitioner,"
        defendant_role = "Respondent."
    else:
        plaintiff_role = "Plaintiff,"
        defendant_role = "Defendant."

    # Assemble caption
    # Fixed-width space alignment breaks in proportional fonts (PDF, Word, browser).
    # Using a natural shorter pad that Claude will reformat to standard US complaint style.
    caption = (
        f"IN THE {court_name}\n"
        f"{venue_line}\n\n"
        f"{plaintiff.upper()},\n"
        f"        {plaintiff_role}\n\n"
        f"v.                              Case No. {case_number}\n\n"
        f"{defendant.upper()},\n"
        f"        {defendant_role}\n"
    )

    logger.debug(
        f"build_court_caption: court={court_name} | district={district} | "
        f"state={state} | case_type={case_type}"
    )

    return caption.strip()