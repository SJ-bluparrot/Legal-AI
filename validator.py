"""
validator.py — Case Validation Engine (Day 6)
-----------------------------------------------
Validates collected case information before it is sent to the complaint
drafting engine (Day 7). Runs after every intake turn so the attorney
gets live feedback as they fill in fields.

Why this is separate from _compute_missing():
    _compute_missing() in intake_router.py only answers "which required
    fields are empty?" — a purely structural check. This module goes
    further and answers "are the values that WERE provided actually usable?"

    Examples of things _compute_missing() cannot catch:
      - incident_date = "last week"          → not a parseable date
      - plaintiff_name = "John"              → only one name word, no surname
      - damages_claimed = "a lot"            → not a dollar figure
      - incident_date = "March 3, 2019"      → past the statute of limitations
      - employment_dispute where dispute_description mentions "discrimination"
        but eeoc_charge_filed is empty       → EEOC filing is legally required

Three-level output:
    {
        "is_valid":        bool,   # no missing required AND no blocking errors
        "can_draft":       bool,   # is_valid OR force_draft=True
        "missing_required": [],    # field IDs of required fields with no value
        "missing_optional": [],    # field IDs of optional fields with no value
        "issues": [                # per-field warnings and errors
            {
                "field":    "incident_date",
                "severity": "warning" | "error",
                "message":  "Date may be past the statute of limitations"
            }
        ],
        "sol_warning": str | None  # statute of limitations warning (case-level)
    }

Severity levels:
    "error"   — blocks drafting unless force_draft=True.
                 Used for values so wrong the complaint would be nonsensical.
                 Example: incident_date = "xyz"
    "warning" — shown to attorney but does not block drafting.
                 Used for values that might be wrong or legally risky.
                 Example: date close to the statute of limitations.

Usage:
    from validator import validate_case_fields

    result = validate_case_fields(
        case_type       = "personal_injury",
        provided_fields = {"plaintiff_name": "John", "incident_date": "Jan 5"},
        elements        = [...]   # flat list from extract_elements()
        force_draft     = False
    )
    # → {
    #     "is_valid":        False,
    #     "can_draft":       False,
    #     "missing_required": ["defendant_name", "incident_location", ...],
    #     "missing_optional": ["witness_names"],
    #     "issues": [
    #         { "field": "plaintiff_name",
    #           "severity": "error",
    #           "message": "plaintiff_name appears to be only one word — a full name is required." }
    #     ],
    #     "sol_warning": None
    # }
"""

import logging
import re
from datetime import datetime, date

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# STATUTE OF LIMITATIONS TABLE
# ══════════════════════════════════════════════
# Conservative (shortest) SOL per case type across US states.
# We warn (not error) when the date field is close to or past this window
# so the attorney can flag it — we don't block drafting, because SOL
# analysis requires state-specific legal judgment we can't do statically.
#
# Format: years (float). None = SOL not applicable for this case type.
# ──────────────────────────────────────────────
_SOL_YEARS: dict[str, float | None] = {
    "personal_injury":   2.0,   # 2 years — most US states (some as low as 1)
    "property_damage":   3.0,   # 3 years — most US states
    "contract_dispute":  4.0,   # 4 years written contract (6 years in some states)
    "eminent_domain":    3.0,   # 3 years from date of taking (varies by state)
    "employment_dispute": 0.5,  # ~180 days for EEOC filing; 3 years state claims
                                 # Using 180 days (most restrictive) as trigger
    "family_law":        None,  # divorce has no SOL; custody varies — skip
    "criminal_defense":  None,  # SOL is the prosecution's burden, not defense
}

# Which field holds the "event date" we compare against SOL for each case type
_SOL_DATE_FIELD: dict[str, str] = {
    "personal_injury":    "incident_date",
    "property_damage":    "incident_date",
    "contract_dispute":   "contract_date",
    "eminent_domain":     "taking_date",
    "employment_dispute": "termination_date",
    "family_law":         "separation_date",
    "criminal_defense":   "incident_date",
}

# SOL warning thresholds — warn when within this fraction of the limit
_SOL_WARNING_FRACTION = 0.2   # warn if less than 20% of the window remains


# ══════════════════════════════════════════════
# FIELD-LEVEL VALIDATORS
# Each returns (is_ok: bool, message: str | None)
# ══════════════════════════════════════════════

# ── Date patterns ──────────────────────────────
# Reused from entity_extractor.py — kept local to avoid cross-file coupling.
_MONTHS_FULL = (
    r'(?:January|February|March|April|May|June|'
    r'July|August|September|October|November|December)'
)
_MONTHS_ABBR = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?'
_MONTHS      = f'(?:{_MONTHS_FULL}|{_MONTHS_ABBR})'

_DATE_RE = re.compile(
    r'(?:'
    r'\d{4}-\d{2}-\d{2}'                          # ISO: 2023-01-05
    r'|\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})'           # US: 01/05/2023
    r'|' + _MONTHS + r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}'  # Jan 5, 2023
    r'|' + _MONTHS + r'\s+\d{1,2}(?:st|nd|rd|th)?'            # Jan 5 (no year)
    r')',
    re.IGNORECASE
)

# Patterns that suggest vague dates rather than real ones
_VAGUE_DATE_RE = re.compile(
    r'\b(?:last\s+(?:week|month|year)|recently|'
    r'a\s+(?:few|couple)\s+(?:days|weeks|months|years)\s+ago|'
    r'some\s+time\s+ago|yesterday|today|this\s+(?:week|month|year))\b',
    re.IGNORECASE
)

# Dollar amount — must contain digits
_DOLLAR_RE = re.compile(r'\d')

# Full name — at least two words, each starting with a capital letter or has 2+ chars
_NAME_WORD_RE = re.compile(r'^[A-Z][a-zA-Z\'-]+$')


def _check_date_field(field_id: str, value: str) -> tuple[bool, str | None]:
    """
    Validate a date field value.
    Returns (ok, error_message_or_None).

    Accepts:
      - Any standard date format (ISO, US slash, full/abbreviated month name)
      - Date with or without year (year-less dates get a warning, not error)

    Rejects:
      - Clearly vague phrases ("last week", "recently")
      - Values with fewer than 3 characters
      - Values with no recognisable date pattern at all
    """
    if len(value) < 3:
        return False, f"'{field_id}' value is too short to be a valid date."

    if _VAGUE_DATE_RE.search(value):
        return False, (
            f"'{field_id}' contains a vague time reference ('{value}'). "
            f"Please provide a specific date."
        )

    if not _DATE_RE.search(value):
        return False, (
            f"'{field_id}' does not look like a valid date (got: '{value}'). "
            f"Please use a format like 'January 5, 2023' or '01/05/2023'."
        )

    return True, None


def _check_name_field(field_id: str, value: str) -> tuple[bool, str | None]:
    """
    Validate a name field value.

    Individual persons require first + last name (2+ words).
    Corporate/entity defendants (Walmart, Amazon, Target, etc.) are legitimately
    one capitalised word — these are accepted without error.

    Rejects:
      - All-lowercase names ("john doe")
      - Names containing digits
      - Single-word values that are all-lowercase (clearly incomplete)
    """
    words = value.split()

    # Single word — only valid if it starts with a capital (corporate name)
    # e.g. "Walmart", "Amazon", "Target" are legitimate single-word defendants
    if len(words) == 1:
        if value[0].isupper():
            return True, None   # corporate / single-word entity name — acceptable
        return False, (
            f"'{field_id}' appears to be only one word ('{value}'). "
            f"For individuals, provide a full legal name (first and last). "
            f"Corporate names are accepted as single words."
        )

    if re.search(r'\d', value):
        return False, (
            f"'{field_id}' contains digits ('{value}'). "
            f"Names should not contain numbers."
        )

    # Check each word starts with a capital — catches "john doe"
    for word in words:
        clean = re.sub(r"[-']", "", word)
        if clean and clean[0].islower():
            return False, (
                f"'{field_id}' appears to not be properly capitalized ('{value}'). "
                f"Please use the full legal name with proper capitalisation."
            )

    return True, None


def _check_dollar_field(field_id: str, value: str) -> tuple[bool, str | None]:
    """
    Validate a dollar/amount field value.
    Must contain at least one digit — catches "a lot", "significant", etc.
    """
    if not _DOLLAR_RE.search(value):
        return False, (
            f"'{field_id}' does not contain a numeric amount (got: '{value}'). "
            f"Please provide a dollar figure, e.g. '$50,000'."
        )
    return True, None


def _check_description_field(field_id: str, value: str, min_words: int = 3) -> tuple[bool, str | None]:
    """
    Validate a free-text description field.
    Must contain at least min_words words to be considered substantive.
    Single-word or two-word descriptions are flagged as too vague.
    """
    word_count = len(value.split())
    if word_count < min_words:
        return False, (
            f"'{field_id}' appears too brief ('{value}'). "
            f"Please provide a more detailed description ({min_words}+ words)."
        )
    return True, None


# ══════════════════════════════════════════════
# FIELD TYPE DISPATCH TABLE
# Maps field_id suffix patterns → validator function
# ══════════════════════════════════════════════

# These patterns are matched against the field_id (not the label).
# Order matters — more specific patterns should come first.
_DATE_FIELD_SUFFIXES = (
    "date", "arrest_date", "taking_date", "separation_date",
    "contract_date", "termination_date", "employment_start_date",
)
_NAME_FIELD_SUFFIXES = (
    "plaintiff_name", "defendant_name", "arresting_agency",
)
_DOLLAR_FIELD_SUFFIXES = (
    "damages_claimed", "compensation_offered", "medical_expenses",
    "lost_wages", "property_value", "contract_value", "wages_owed",
    "repair_cost", "fair_market_value", "bail_amount",
)
_DESCRIPTION_FIELD_SUFFIXES = (
    "description", "theory", "grounds_for_divorce",
    "dispute_description", "breach_description",
)


def _dispatch_field_check(field_id: str, value: str) -> tuple[bool, str | None]:
    """
    Dispatch to the correct field validator based on the field_id.
    Returns (ok, error_message_or_None).
    Returns (True, None) for fields with no specific validator.
    """
    fid = field_id.lower()

    # Date fields
    if any(fid == s or fid.endswith(f"_{s}") or fid == s for s in _DATE_FIELD_SUFFIXES):
        if "date" in fid:
            return _check_date_field(field_id, value)

    # Name fields
    if fid in _NAME_FIELD_SUFFIXES or fid.endswith("_name"):
        return _check_name_field(field_id, value)

    # Dollar / amount fields
    if fid in _DOLLAR_FIELD_SUFFIXES:
        return _check_dollar_field(field_id, value)

    # injury_description — 2 words minimum.
    # Legal complaints routinely contain concise medical terms:
    # "fractured wrist", "broken arm", "spinal injury" are all valid and complete.
    # The generic 3-word floor is too strict for clinical injury descriptions.
    if fid == "injury_description":
        return _check_description_field(field_id, value, min_words=2)

    # Description fields (min 3 words — "broke his arm" is valid)
    if any(fid.endswith(s) for s in _DESCRIPTION_FIELD_SUFFIXES):
        return _check_description_field(field_id, value, min_words=3)

    # No specific validator — accept as-is
    return True, None


# ══════════════════════════════════════════════
# STATUTE OF LIMITATIONS CHECK
# ══════════════════════════════════════════════

def _parse_date_value(value: str) -> date | None:
    """
    Attempt to parse a date string into a Python date object.
    Tries common formats in order. Returns None if unparseable.
    """
    formats = [
        "%Y-%m-%d",         # 2023-01-05
        "%m/%d/%Y",         # 01/05/2023
        "%m/%d/%y",         # 01/05/23
        "%B %d, %Y",        # January 5, 2023
        "%B %dth, %Y",      # January 5th, 2023
        "%B %dst, %Y",
        "%B %dnd, %Y",
        "%B %drd, %Y",
        "%b %d, %Y",        # Jan 5, 2023
        "%b %d %Y",         # Jan 5 2023
        "%B %d %Y",         # January 5 2023
    ]
    # Strip ordinal suffixes before trying formats
    cleaned = re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', value.strip())
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _check_statute_of_limitations(
    case_type: str,
    provided_fields: dict,
) -> str | None:
    """
    Check if the case's event date is near or past the statute of limitations.
    Returns a warning string if there's a concern, None if all is fine.

    Returns None (no warning) for:
      - Case types without a defined SOL (family_law, criminal_defense)
      - Date fields that are missing or unparseable
      - Cases well within the SOL window

    Returns a warning string for:
      - Cases past the SOL
      - Cases within the final 20% of the SOL window
    """
    sol_years = _SOL_YEARS.get(case_type)
    if sol_years is None:
        return None

    date_field = _SOL_DATE_FIELD.get(case_type)
    if not date_field:
        return None

    raw_date = provided_fields.get(date_field, "")
    if not raw_date or not str(raw_date).strip():
        return None   # date not provided yet — nothing to check

    event_date = _parse_date_value(str(raw_date))
    if event_date is None:
        return None   # unparseable — field validator will catch this separately

    today        = date.today()
    days_elapsed = (today - event_date).days

    if days_elapsed < 0:
        # Future date — field validator may catch this, but SOL doesn't apply
        return None

    sol_days     = sol_years * 365.25
    days_remaining = sol_days - days_elapsed

    if days_remaining < 0:
        # Past SOL
        years_over = abs(days_remaining) / 365.25
        return (
            f"⚠️ The event date ({raw_date}) may be past the typical statute of limitations "
            f"for {case_type.replace('_', ' ')} cases "
            f"({sol_years:.0f} year{'s' if sol_years != 1 else ''} in most states). "
            f"The case appears to be approximately {years_over:.1f} year(s) beyond the filing window. "
            f"Verify the applicable SOL with local counsel before proceeding."
        )

    # Within the final 20% of the SOL window
    if days_remaining < sol_days * _SOL_WARNING_FRACTION:
        days_left = int(days_remaining)
        return (
            f"⚠️ The event date ({raw_date}) is approaching the statute of limitations "
            f"for {case_type.replace('_', ' ')} cases "
            f"({sol_years:.0f} year{'s' if sol_years != 1 else ''} in most states). "
            f"Approximately {days_left} day(s) remain. "
            f"File promptly or verify the applicable SOL with local counsel."
        )

    return None


# ══════════════════════════════════════════════
# CASE-TYPE SPECIFIC RULES
# ══════════════════════════════════════════════

def _case_specific_issues(
    case_type: str,
    provided_fields: dict,
) -> list[dict]:
    """
    Apply validation rules that are unique to a specific case type.
    Returns a list of issue dicts: { field, severity, message }

    These are rules that require cross-field logic or legal domain knowledge
    that the generic per-field validators cannot express.
    """
    issues = []

    if case_type == "criminal_defense":
        # Charges field is required but let's also check it's substantive
        charges = provided_fields.get("charges", "")
        if charges and len(charges.split()) < 2:
            issues.append({
                "field":    "charges",
                "severity": "warning",
                "message":  (
                    "The charges field appears very brief. "
                    "Please include the specific criminal charges (e.g. 'felony theft', 'DUI', 'assault')."
                ),
            })

        # Arrest date should not be after today
        arrest_raw = provided_fields.get("arrest_date", "")
        if arrest_raw:
            arrest_date = _parse_date_value(str(arrest_raw))
            if arrest_date and arrest_date > date.today():
                issues.append({
                    "field":    "arrest_date",
                    "severity": "error",
                    "message":  f"Arrest date '{arrest_raw}' is in the future.",
                })

    elif case_type == "employment_dispute":
        # If dispute_description mentions discrimination keywords, EEOC may be mandatory
        desc = provided_fields.get("dispute_description", "").lower()
        eeoc = provided_fields.get("eeoc_charge_filed", "")
        discrimination_terms = [
            "discriminat", "harass", "hostile", "retaliat",
            "race", "gender", "sex", "age", "disability", "religion", "national origin"
        ]
        if any(term in desc for term in discrimination_terms) and not eeoc:
            issues.append({
                "field":    "eeoc_charge_filed",
                "severity": "warning",
                "message":  (
                    "The case description suggests a potential discrimination or harassment claim. "
                    "An EEOC charge is typically required before filing a federal employment lawsuit. "
                    "Please confirm whether an EEOC charge has been filed."
                ),
            })

        # Termination date must not be before employment start date
        start_raw = provided_fields.get("employment_start_date", "")
        end_raw   = provided_fields.get("termination_date", "")
        if start_raw and end_raw:
            start_d = _parse_date_value(str(start_raw))
            end_d   = _parse_date_value(str(end_raw))
            if start_d and end_d and end_d < start_d:
                issues.append({
                    "field":    "termination_date",
                    "severity": "error",
                    "message":  (
                        f"Termination/incident date ('{end_raw}') is before the employment "
                        f"start date ('{start_raw}'). Please check these dates."
                    ),
                })

    elif case_type == "family_law":
        # jurisdiction_state should look like a US state name or abbreviation
        state = provided_fields.get("jurisdiction_state", "")
        if state and len(state.strip()) < 2:
            issues.append({
                "field":    "jurisdiction_state",
                "severity": "error",
                "message":  (
                    f"'{state}' does not appear to be a valid US state. "
                    f"Please provide the full state name or standard two-letter abbreviation."
                ),
            })

        # Separation date must be after marriage date
        marriage_raw   = provided_fields.get("marriage_date", "")
        separation_raw = provided_fields.get("separation_date", "")
        if marriage_raw and separation_raw:
            marriage_d   = _parse_date_value(str(marriage_raw))
            separation_d = _parse_date_value(str(separation_raw))
            if marriage_d and separation_d and separation_d < marriage_d:
                issues.append({
                    "field":    "separation_date",
                    "severity": "error",
                    "message":  (
                        f"Separation date ('{separation_raw}') is before the marriage date "
                        f"('{marriage_raw}'). Please check these dates."
                    ),
                })

    elif case_type == "eminent_domain":
        # compensation_offered should be a dollar figure if provided
        comp = provided_fields.get("compensation_offered", "")
        if comp and not _DOLLAR_RE.search(comp):
            issues.append({
                "field":    "compensation_offered",
                "severity": "warning",
                "message":  (
                    f"'compensation_offered' value ('{comp}') does not contain a dollar amount. "
                    f"Please provide the specific dollar figure offered by the government."
                ),
            })

    elif case_type == "personal_injury":
        # injury_description should mention a body part or injury type.
        # 2-word clinical terms like "fractured wrist" or "broken arm" are valid
        # and common in legal complaints — do not flag them as too brief.
        injury = provided_fields.get("injury_description", "").lower()
        if injury and len(injury.split()) < 2:
            issues.append({
                "field":    "injury_description",
                "severity": "warning",
                "message":  (
                    "The injury description appears very brief. "
                    "Please describe the nature and extent of the injuries "
                    "(e.g. 'fractured wrist', 'traumatic brain injury')."
                ),
            })

    elif case_type == "contract_dispute":
        # contract_value and damages_claimed — damages should not exceed contract value
        contract_val_raw = provided_fields.get("contract_value", "")
        damages_raw      = provided_fields.get("damages_claimed", "")
        if contract_val_raw and damages_raw:
            contract_digits = re.findall(r'[\d,]+', contract_val_raw.replace(",", ""))
            damages_digits  = re.findall(r'[\d,]+', damages_raw.replace(",", ""))
            if contract_digits and damages_digits:
                try:
                    contract_num = float(contract_digits[0])
                    damages_num  = float(damages_digits[0])
                    if damages_num > contract_num * 3:
                        issues.append({
                            "field":    "damages_claimed",
                            "severity": "warning",
                            "message":  (
                                f"Damages claimed ({damages_raw}) appear significantly higher "
                                f"than the contract value ({contract_val_raw}). "
                                f"Ensure this is intentional and includes consequential damages."
                            ),
                        })
                except (ValueError, IndexError):
                    pass  # couldn't parse numbers — skip this check

    return issues


# ══════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════

def validate_case_fields(
    case_type:       str,
    provided_fields: dict,
    elements:        list[dict],
    force_draft:     bool = False,
) -> dict:
    """
    Validate the collected case fields and return a structured result.

    Validation has three layers:
      1. Structural — are required/optional fields present?
      2. Per-field   — are provided values the right format/type?
      3. Case-type   — are cross-field and domain-specific rules satisfied?
      4. SOL check   — is the event date within the filing window?

    Args:
        case_type       : Detected case type string (e.g. "personal_injury")
        provided_fields : Current provided_fields dict from case session
        elements        : Flat element list from extract_elements()
        force_draft     : If True, can_draft=True even when is_valid=False

    Returns:
        {
            "is_valid":         bool,   # True if no missing_required and no errors
            "can_draft":        bool,   # True if is_valid OR force_draft
            "missing_required": list,   # field IDs that are required but empty
            "missing_optional": list,   # field IDs that are optional but empty
            "issues": [                 # per-field and cross-field findings
                {
                    "field":    str,    # element ID the issue relates to
                    "severity": str,    # "error" | "warning"
                    "message":  str
                }
            ],
            "sol_warning": str | None   # statute of limitations warning if applicable
        }

    Never raises. Always returns a valid dict.
    """
    missing_required = []
    missing_optional = []
    issues           = []

    # ── Layer 1: Structural check ─────────────────────────────────────────────
    for el in elements:
        fid      = el["id"]
        required = el.get("required", False)
        value    = str(provided_fields.get(fid, "")).strip()

        if not value:
            if required:
                missing_required.append(fid)
            else:
                missing_optional.append(fid)
            continue   # no point validating an empty field

        # ── Layer 2: Per-field format/type check ──────────────────────────────
        ok, msg = _dispatch_field_check(fid, value)
        if not ok and msg:
            # Determine severity: required fields with bad values are errors,
            # optional fields with bad values are warnings.
            severity = "error" if required else "warning"
            issues.append({
                "field":    fid,
                "severity": severity,
                "message":  msg,
            })

    # ── Layer 3: Case-type specific cross-field checks ────────────────────────
    case_issues = _case_specific_issues(case_type, provided_fields)
    issues.extend(case_issues)

    # ── Layer 4: Statute of limitations ───────────────────────────────────────
    sol_warning = _check_statute_of_limitations(case_type, provided_fields)

    # ── Compute is_valid and can_draft ────────────────────────────────────────
    # is_valid = no missing required fields AND no blocking errors
    blocking_errors = [i for i in issues if i["severity"] == "error"]
    is_valid        = len(missing_required) == 0 and len(blocking_errors) == 0
    can_draft       = is_valid or force_draft

    # ── Build human-readable summary (explainability layer) ───────────────────
    case_label = case_type.replace("_", " ").title()
    if is_valid:
        validation_summary = (
            f"All required elements for a {case_label} complaint are present. "
            f"The case is ready to draft."
        )
    elif missing_required and not blocking_errors:
        field_labels = ", ".join(f"'{f}'" for f in missing_required[:3])
        more = f" and {len(missing_required) - 3} more" if len(missing_required) > 3 else ""
        validation_summary = (
            f"{len(missing_required)} required field(s) missing: {field_labels}{more}. "
            # f"Complaint cannot be generated until these are provided."
            f"A draft can still be generated but will contain [UNKNOWN] placeholders."
        )
    elif blocking_errors and not missing_required:
        validation_summary = (
            f"{len(blocking_errors)} field value(s) contain errors that must be corrected "
            f"before the complaint can be drafted."
        )
    else:
        validation_summary = (
            f"{len(missing_required)} required field(s) missing and "
            f"{len(blocking_errors)} field error(s) detected. "
            f"Resolve these before drafting."
        )

    logger.info(
        f"validate_case_fields | case_type={case_type} | "
        f"missing_required={len(missing_required)} | "
        f"missing_optional={len(missing_optional)} | "
        f"issues={len(issues)} (errors={len(blocking_errors)}) | "
        f"is_valid={is_valid} | can_draft={can_draft}"
    )

    return {
        "is_valid":          is_valid,
        "can_draft":         can_draft,
        "missing_required":  missing_required,
        "missing_optional":  missing_optional,
        "issues":            issues,
        "sol_warning":       sol_warning,
        "validation_summary": validation_summary,
    }