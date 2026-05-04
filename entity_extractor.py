"""
entity_extractor.py — Regex Field Extraction from Prose
---------------------------------------------------------
Extracts structured case fields from free-form attorney prose using
fast regex patterns (Layer 0 only). No model inference required.

Output contract:
    Returns a flat dict of { element_id: extracted_value } for every field
    the module could identify in the text. Only populated fields are returned.

    Example:
        {
            "plaintiff_name":    "John Doe",
            "defendant_name":    "Mark Smith",
            "incident_date":     "January 5",
            "incident_location": "Sunset Blvd"
        }

Usage:
    from entity_extractor import extract_entities

    extracted, sources = extract_entities(
        text="John Doe was hit by Mark Smith on Jan 5 on Sunset Blvd.",
        case_type="personal_injury",
        required_element_ids=["plaintiff_name", "defendant_name", "incident_date"],
    )
"""

import logging
import re

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# LAYER 0 — REGEX PRE-PASS
# ══════════════════════════════════════════════
# All patterns compiled once at import time for speed.
# Each pattern targets one class of legal entity.
# ──────────────────────────────────────────────

# ── Date patterns ──────────────────────────────
# Ordered from most specific to least (ISO > full name+year > abbr+year > name only)
_MONTHS_FULL = (
    r'(?:January|February|March|April|May|June|'
    r'July|August|September|October|November|December)'
)
_MONTHS_ABBR = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?'
_MONTHS      = f'(?:{_MONTHS_FULL}|{_MONTHS_ABBR})'
_ORDINAL     = r'(?:st|nd|rd|th)?'

_DATE_PATTERNS = [
    # ISO: 2023-01-05
    re.compile(r'\b(\d{4}-\d{2}-\d{2})\b'),
    # US slash: 01/05/2023 or 01/05/23
    re.compile(r'\b(\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))\b'),
    # Full month name + day + year: January 5, 2023 or January 5th 2023
    re.compile(
        rf'\b({_MONTHS}\s+\d{{1,2}}{_ORDINAL},?\s+\d{{4}})\b',
        re.IGNORECASE
    ),
    # Abbreviated month + day + year: Jan 5, 2023
    re.compile(
        rf'\b({_MONTHS_ABBR}\s+\d{{1,2}}{_ORDINAL},?\s+\d{{4}})\b',
        re.IGNORECASE
    ),
    # Month name + day only (no year): January 5th, Jan 5
    re.compile(
        rf'\b({_MONTHS}\s+\d{{1,2}}{_ORDINAL})\b',
        re.IGNORECASE
    ),
]

# Date fields in priority order — the first field found in required_element_ids
# gets the first extracted date, the second field gets the second date (if found), etc.
# This handles cases like criminal_defense which needs both arrest_date and incident_date.
_DATE_FIELD_PRIORITY = [
    "incident_date",
    "arrest_date",
    "taking_date",
    "contract_date",
    "termination_date",
    "marriage_date",
    "employment_start_date",
    "separation_date",
]

# ── Dollar amount patterns ─────────────────────
_DOLLAR_PATTERNS = [
    # $50,000 or $50,000.00 — standard currency notation
    re.compile(r'\$([\d,]+(?:\.\d{2})?)'),
    # 50000 dollars / 50,000.00 USD
    re.compile(r'\b([\d,]+(?:\.\d{2})?)\s*(?:dollars?|USD)\b', re.IGNORECASE),
]

# Dollar fields in priority order (used as fallback when no keyword context matches)
_DOLLAR_FIELD_PRIORITY = [
    "damages_claimed",
    "compensation_offered",
    "medical_expenses",
    "lost_wages",
    "property_value",
    "contract_value",
    "wages_owed",
    "repair_cost",
    "fair_market_value",
    "bail_amount",
]

# Keyword context map — words that appear near a dollar amount strongly suggest
# which field the amount belongs to.  Each entry maps a field_id to a list of
# keywords that indicate that field.  The window checked is ±60 characters
# around the dollar sign so we catch "medical expenses costing $18,500" and
# "$18,500 in medical treatment" equally.
_DOLLAR_CONTEXT_KEYWORDS: dict[str, list[str]] = {
    "medical_expenses":    ["medical", "hospital", "treatment", "surgery",
                            "therapy", "doctor", "clinic", "ambulance", "care"],
    "lost_wages":          ["wage", "salary", "income", "pay", "earning",
                            "lost wages", "unpaid", "compensation"],
    "repair_cost":         ["repair", "fix", "restoration", "rebuild", "replace"],
    "bail_amount":         ["bail", "bond"],
    "contract_value":      ["contract", "agreement", "deal"],
    "compensation_offered":["offered", "offer", "settlement", "government paid"],
    "damages_claimed":     ["damages", "total damages", "claim", "seeking",
                            "demand", "judgment"],
}

# ── Name patterns ──────────────────────────────
# Plaintiff / employee / client — explicit legal phrases only.
# Avoid generic "John hit Mark" → false positives without phrase context.
_PLAINTIFF_PATTERNS = [
    # "My client, Jane Doe" or "my client Jane Doe" — comma optional
    re.compile(
        r'(?:my client|the plaintiff|plaintiff is|claimant is|claimant)[,\s]+(?:is\s+)?'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:I represent|representing|on behalf of)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:the employee|the worker|employee)\s+(?:named?\s+)?'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:the petitioner|petitioner is)[,\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        re.IGNORECASE
    ),
    # ALL CAPS caption format: "JANE DOE,\n   Plaintiff"
    re.compile(
        r'^([A-Z]{2,}(?:\s+[A-Z]{2,})+),?\s*\n\s*(?:Plaintiff|Petitioner)',
        re.MULTILINE
    ),
]

# Defendant / employer / accused
_DEFENDANT_PATTERNS = [
    # "the defendant, Whole Foods Market Group, Inc." — comma optional
    re.compile(
        r'(?:the defendant|defendant is|against defendant)[,\s]+(?:is\s+)?'
        r'([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*(?:,?\s*(?:Inc|LLC|Corp|Ltd|Co)\.?)?)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:suing|against|sued?)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:Inc|LLC|Corp|Ltd|Co)\.?)?)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:the employer|the company|employer is|company is)[,\s]+(?:is\s+)?'
        r'([A-Z][a-zA-Z\s,\.]+?)(?:\.|,|\s+(?:fired|terminated|harassed|underpaid))',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:struck by|hit by|caused by|negligence of|owned by)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:the government entity|the government|city of|county of|state of)\s+'
        r'([A-Z][a-zA-Z\s]+?)(?:\.|,|\s+(?:seized|took|condemned))',
        re.IGNORECASE
    ),
    # ALL CAPS caption format: "WHOLE FOODS MARKET GROUP, INC.,\n   Defendant"
    re.compile(
        r'^([A-Z]{2,}(?:\s+[A-Z]{2,})*(?:,?\s*(?:INC|LLC|CORP|LTD|CO)\.?)?),?\s*\n\s*(?:Defendant|Respondent)',
        re.MULTILINE
    ),
]

# ── Location patterns ──────────────────────────
# Ordered from most specific to least — first match wins.
# Intersection must come before venue so "Pine Street and 8th Avenue"
# is not collapsed into "the intersection" by the venue pattern.
_STREET_TYPES = (
    r'(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|'
    r'Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Highway|Hwy|Parkway|Pkwy)'
)

# Street name token — matches either:
#   a) a named word:           "Pine", "Main", "Oak"
#   b) a numeric ordinal:      "8th", "1st", "22nd"
# This is needed because cross-streets like "8th Avenue" start with a digit.
_STREET_NAME_TOKEN = r'(?:\d+(?:st|nd|rd|th)?|[A-Z][a-zA-Z]+)'

_LOCATION_PATTERNS = [
    # Numbered address: "123 Main Street", "456 Oak Ave"
    re.compile(
        r'\b(\d+\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*'
        r'\s+' + _STREET_TYPES + r'\.?)\b',
        re.IGNORECASE
    ),
    # Street intersection: "Pine Street and 8th Avenue"
    # Also matches: "Oak Ave and Main St", "1st Street and Broadway"
    # Both sides use _STREET_NAME_TOKEN to handle ordinal names like "8th" or "1st".
    re.compile(
        r'(' + _STREET_NAME_TOKEN + r'\s+' + _STREET_TYPES + r'\.?'
        r'\s+and\s+'
        + _STREET_NAME_TOKEN + r'\s+' + _STREET_TYPES + r'\.?)',
        re.IGNORECASE
    ),
    # "intersection of Pine Street and 8th Avenue" — strips the prefix phrase
    re.compile(
        r'intersection\s+of\s+'
        r'(' + _STREET_NAME_TOKEN + r'\s+' + _STREET_TYPES + r'\.?'
        r'\s+and\s+'
        + _STREET_NAME_TOKEN + r'\s+' + _STREET_TYPES + r'\.?)',
        re.IGNORECASE
    ),
    # Named venue: "at the grocery store", "at the hospital" — kept as last resort
    # This is intentionally LAST so a specific address always wins over a venue label.
    re.compile(
        r'\bat\s+(?:the\s+)?([A-Z][a-zA-Z\s\']+?'
        r'(?:Store|Mall|Hospital|Clinic|School|Office|Restaurant|Building|'
        r'Center|Centre|Park|Station|Intersection|Corner))\b',
        re.IGNORECASE
    ),
]

# ── Police / case report number ────────────────
_REPORT_PATTERNS = [
    re.compile(
        r'(?:police report|report|case)\s*(?:number|no\.?|#|num\.?)\s*([A-Z0-9][A-Z0-9\-]+)',
        re.IGNORECASE
    ),
    re.compile(
        r'report\s+(?:number\s+|#\s*)?(\d{4,})',
        re.IGNORECASE
    ),
]

# ── Job title pattern ──────────────────────────
_JOB_TITLE_PATTERNS = [
    re.compile(
        r'(?:worked as|position of|job title|role of|employed as|hired as|title of)\s+'
        r'(?:a\s+|an\s+)?([A-Za-z][a-zA-Z\s]+?)(?:\.|,|\s+at|\s+for|\s+since|\s+from|\s+until)',
        re.IGNORECASE
    ),
]


def _extract_all_dates(text: str) -> list[str]:
    """Return all date strings found in text, in order of appearance, deduplicated."""
    found = []
    seen  = set()
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(1).strip()
            if val.lower() not in seen:
                seen.add(val.lower())
                found.append(val)
    # Sort by position in text for natural order
    positions = {}
    for val in found:
        idx = text.lower().find(val.lower())
        positions[val] = idx if idx != -1 else 9999
    return sorted(found, key=lambda v: positions.get(v, 9999))


def _normalise_dollar(raw: str) -> str | None:
    """Normalise a raw dollar string to e.g. '$18,500'. Returns None on failure."""
    try:
        val = float(raw.replace(",", ""))
        if val == int(val):
            return f"${int(val):,}"
        return f"${val:,.2f}"
    except ValueError:
        return None


def _extract_contextual_dollars(
    text: str,
    required_field_ids: set,
) -> dict[str, str]:
    """
    Extract dollar amounts from text and assign each to the most contextually
    appropriate field using keyword proximity, falling back to priority order.

    Returns { field_id: normalised_dollar_string } — only fields in
    required_field_ids are included.  Each dollar amount is assigned to AT
    MOST ONE field, preventing the same figure from populating both
    medical_expenses and damages_claimed when the attorney writes:
        "she incurred $18,500 in medical expenses"

    Algorithm:
      1. Find every dollar mention and record its character position.
      2. For each mention, inspect the ±60-char window for context keywords.
      3. If a keyword maps to a required field → assign to that field.
      4. If no keyword matches → assign to the first available priority field.
      5. Each field is filled at most once (first match wins).
    """
    # ── Step 1: collect all dollar mentions with positions ────────────────────
    mentions = []   # list of (char_pos, normalised_value)
    for pat in _DOLLAR_PATTERNS:
        for m in pat.finditer(text):
            val = _normalise_dollar(m.group(1))
            if val:
                mentions.append((m.start(), val))

    if not mentions:
        return {}

    # Deduplicate by value (same amount at different positions counted once)
    seen_vals: set[str] = set()
    unique_mentions = []
    for pos, val in sorted(mentions):    # sorted by position in text
        if val not in seen_vals:
            seen_vals.add(val)
            unique_mentions.append((pos, val))

    # ── Step 2 & 3: assign via keyword context ────────────────────────────────
    result:         dict[str, str] = {}
    assigned_fields: set[str]      = set()

    for pos, val in unique_mentions:
        # Extract ±60 char window around the dollar sign
        window_start = max(0, pos - 60)
        window_end   = min(len(text), pos + 60)
        window       = text[window_start:window_end].lower()

        matched_field = None
        for field_id, keywords in _DOLLAR_CONTEXT_KEYWORDS.items():
            if field_id not in required_field_ids:
                continue
            if field_id in assigned_fields:
                continue
            if any(kw in window for kw in keywords):
                matched_field = field_id
                break

        if matched_field:
            result[matched_field]      = val
            assigned_fields.add(matched_field)

    # ── Step 4: fallback — any remaining mentions fill priority-ordered fields ─
    # Only runs for amounts that had no keyword context (e.g. bare "$50,000").
    unassigned_mentions = [(p, v) for p, v in unique_mentions if v not in result.values()]
    for _, val in unassigned_mentions:
        for field_id in _DOLLAR_FIELD_PRIORITY:
            if field_id in required_field_ids and field_id not in assigned_fields:
                result[field_id]      = val
                assigned_fields.add(field_id)
                break   # one unmatched amount → one fallback field

    logger.debug(f"Dollar extraction: {result}")
    return result


def _extract_name(text: str, patterns: list) -> str | None:
    """Try each pattern in order, return first match.

    After regex capture, we walk the matched words left-to-right and keep
    only leading title-cased words. ALL CAPS names (from complaint captions)
    are title-cased before storage.

    Example:
        regex captures  → "Sarah Williams was struck by"  (IGNORECASE bleeds)
        after filtering → "Sarah Williams"                (stops at 'was')
        ALL CAPS        → "JANE DOE"  → stored as "Jane Doe"
    """
    for pat in patterns:
        m = pat.search(text)
        if m:
            raw = m.group(1).strip().rstrip(".,")
            # ALL CAPS caption names → convert to Title Case first
            if raw == raw.upper() and len(raw) > 2:
                raw = raw.title()
            # Keep only words that start with an uppercase letter.
            # Stops at the first word that is all-lowercase (verbs, prepositions).
            title_words = []
            for word in raw.split():
                if word[0].isupper():
                    title_words.append(word)
                else:
                    break
            name  = " ".join(title_words)
            words = name.split()
            # Sanity: at least two words, no more than 6 words (company names can be longer)
            if 2 <= len(words) <= 6:
                return name
    return None


def _extract_location(text: str) -> str | None:
    """Return first location match from address or named-venue patterns."""
    for pat in _LOCATION_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip().rstrip(".,")
    return None


def _extract_report_number(text: str) -> str | None:
    """Return first police/case report number found."""
    for pat in _REPORT_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


def _extract_job_title(text: str) -> str | None:
    """Return first job title found in text."""
    for pat in _JOB_TITLE_PATTERNS:
        m = pat.search(text)
        if m:
            title = m.group(1).strip().rstrip(".,")
            # Sanity: 1–5 words, not a full sentence
            words = title.split()
            if 1 <= len(words) <= 5:
                return title
    return None


def _regex_prepass(text: str, required_element_ids: list[str]) -> tuple[dict, dict]:
    """
    Layer 0: extract high-confidence fields using regex — no GPU required.

    Runs before the model. Any field populated here is excluded from the
    model prompt, saving tokens and reducing GPU inference time.

    Returns:
        (values, sources) — two dicts with the same keys.
        values : { element_id: extracted_value }
        sources: { element_id: "regex" }  — all regex fields tagged at source level 2

    Only populates fields that are in required_element_ids.
    """
    ids      = set(required_element_ids)
    result   = {}
    sources  = {}

    # ── Dates ────────────────────────────────────
    # Extract all dates found in text, then assign them to date fields in priority order.
    # Example: criminal_defense has both arrest_date and incident_date.
    #   "My client was arrested on Jan 5 for an incident that occurred Dec 20."
    #   → arrest_date=Jan 5, incident_date=Dec 20
    dates      = _extract_all_dates(text)
    date_slots = [f for f in _DATE_FIELD_PRIORITY if f in ids]
    for i, field_id in enumerate(date_slots):
        if i < len(dates):
            result[field_id]  = dates[i]
            sources[field_id] = "regex"

    # ── Dollar amounts ────────────────────────────
    # Context-aware extraction: maps each amount to the most appropriate field
    # using keyword proximity so "medical expenses costing $18,500" only fills
    # medical_expenses, not also damages_claimed.
    dollar_assignments = _extract_contextual_dollars(text, ids)
    for field_id, val in dollar_assignments.items():
        result[field_id]  = val
        sources[field_id] = "regex"

    # ── Names ─────────────────────────────────────
    if "plaintiff_name" in ids:
        name = _extract_name(text, _PLAINTIFF_PATTERNS)
        if name:
            result["plaintiff_name"]  = name
            sources["plaintiff_name"] = "regex"

    if "defendant_name" in ids:
        name = _extract_name(text, _DEFENDANT_PATTERNS)
        if name:
            result["defendant_name"]  = name
            sources["defendant_name"] = "regex"

    # ── Location ──────────────────────────────────
    for loc_field in ("incident_location", "property_address"):
        if loc_field in ids:
            loc = _extract_location(text)
            if loc:
                result[loc_field]  = loc
                sources[loc_field] = "regex"
                break

    # ── Police / report number ────────────────────
    if "police_report_number" in ids:
        num = _extract_report_number(text)
        if num:
            result["police_report_number"]  = num
            sources["police_report_number"] = "regex"

    # ── Job title (employment_dispute) ────────────
    if "job_title" in ids:
        title = _extract_job_title(text)
        if title:
            result["job_title"]  = title
            sources["job_title"] = "regex"

    if result:
        logger.info(f"Regex pre-pass extracted {len(result)} field(s): {list(result.keys())}")
    return result, sources


def _clean_entity_value(field_id: str, value: str) -> str:
    """
    Post-process a model-extracted value to remove surrounding context words.

    LLM extraction sometimes returns partial sentences instead of the core entity:
        plaintiff_name   : "John Doe slipped on" → "John Doe"
        incident_location: "5 on Sunset Blvd"    → "Sunset Blvd"
        negligence_act   : "failed to clean..."  → kept as-is (description field)

    Rules:
      - Name fields      : walk words left-to-right, stop at any verb or connector
      - Location fields  : strip leading digits and prepositions
      - Description fields: NOT stripped aggressively — full phrase is the value
      - All other fields : strip trailing connector words only
    """
    v = value.strip()

    # ── Description fields — return as-is, do NOT strip ──────────────────────
    # Stripping "to/for/in/of" destroys meaningful phrases like
    # "failed to clean the wet floor" → we want the full phrase preserved.
    _DESCRIPTION_FIELD_IDS = (
        "negligence_act", "injury_description", "damage_description",
        "dispute_description", "breach_description", "defense_theory",
        "grounds_for_divorce", "public_use_stated", "contract_description",
    )
    if field_id in _DESCRIPTION_FIELD_IDS:
        return v.strip('.,;:- ') or value

    # ── Strip trailing noise for non-description fields ───────────────────────
    # Catches: "John Doe was rear-ended" → "John Doe"
    # Extended verb list covers past-tense: slipped, fell, broke, hit, etc.
    v = re.sub(
        r'\s+(?:was|is|were|has|had|have|being|been|'
        r'slipped|fell|broke|hit|crashed|struck|injured|'
        r'by|on|at|and|to|the|for|in|of|a)\b.*$',
        '', v, flags=re.IGNORECASE
    ).strip()

    # ── Name fields: collect capitalised words, stop at any verb ─────────────
    if field_id.endswith("_name"):
        words = v.split()
        name_words = []
        _STOP_WORDS = {
            'was', 'is', 'were', 'has', 'had', 'on', 'at', 'by', 'and',
            'the', 'a', 'an', 'in', 'of', 'rear', 'hit', 'sued', 'fired',
            'arrested', 'slipped', 'fell', 'broke', 'crashed', 'struck',
            'injured', 'terminated', 'harassed', 'accused', 'charged',
        }
        for word in words:
            if word.lower() in _STOP_WORDS:
                break
            if re.search(r'\d', word):
                break
            name_words.append(word)
            if len(name_words) == 4:   # cap at 4 — "Mary Jo Van Buren"
                break
        if name_words:
            v = ' '.join(name_words)

    # ── Location fields: strip leading digits and prepositions ───────────────
    if field_id in ('incident_location', 'property_address'):
        v = re.sub(r'^\d+\s+(?:on\s+|at\s+|in\s+)?', '', v, flags=re.IGNORECASE)
        v = re.sub(r'^(?:on|at|in)\s+', '', v, flags=re.IGNORECASE)
        v = v.strip()

    # ── Final cleanup ─────────────────────────────────────────────────────────
    v = v.strip('.,;:- ')
    return v if v else value


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def extract_entities(
    text: str,
    case_type: str,
    required_element_ids: list[str],
    model=None,
    tokenizer=None,
) -> tuple[dict, dict]:
    """
    Extract structured case fields from free-form attorney prose using regex.

    Args:
        text                 : The attorney's free-form message
        case_type            : Detected case type (e.g. "personal_injury")
        required_element_ids : List of element IDs from extract_elements()
        model                : Ignored (kept for API compatibility)
        tokenizer            : Ignored (kept for API compatibility)

    Returns:
        (values, sources) — two dicts with the same keys.
        values : { element_id: extracted_value } for all fields found by regex
        sources: { element_id: "regex" }
    """
    if not text or not text.strip():
        return {}, {}

    if not required_element_ids:
        logger.warning("extract_entities called with empty required_element_ids — skipping.")
        return {}, {}

    text = text[:1500]

    merged, merged_sources = _regex_prepass(text, required_element_ids)

    # Drop any value that contains the word "unknown" — treat as unset
    unknown_keys = {k for k, v in merged.items() if "unknown" in str(v).lower().strip()}
    for k in unknown_keys:
        merged.pop(k, None)
        merged_sources.pop(k, None)

    if merged:
        logger.info(f"Regex extracted {len(merged)} field(s): {list(merged.keys())}")

    return merged, merged_sources


def merge_provided_fields(
    existing: dict,
    newly_extracted: dict,
    allow_overwrite: bool = False,
    existing_sources: dict | None = None,
    new_sources:      dict | None = None,
) -> tuple[dict, dict]:
    """
    Merge newly extracted fields into the session's existing provided_fields,
    using source-priority logic to decide which value wins on conflict.

    Priority table (defined in utils.FIELD_SOURCE_PRIORITY):
        llm (1) < regex (2) < human (3) < corrected (4)

    Rules when allow_overwrite=False (used at /intake/start):
        Existing non-empty values are NOT overwritten at all — this prevents
        auto-extracted values from clobbering each other on the very first turn.

    Rules when allow_overwrite=True (used at /intake/provide):
        1. If the field is empty → always fill it.
        2. If new source has HIGHER priority than existing → replace.
           Example: regex (2) replaces llm (1) even if new value is shorter.
           THIS IS THE BUG FIX: previously only word-count governed overwrites.
        3. If new source has EQUAL priority → replace only if new value is
           shorter (cleaner). This handles duplicate extractions from the same
           source tier (e.g. model output on two consecutive turns).
        4. If new source has LOWER priority → do nothing (existing is better).

        Existing flat fields with no source recorded are treated as "human" (3)
        to protect manually-entered attorney data from being overwritten by any
        automated extraction.

    Args:
        existing          : Current provided_fields dict from case session
        newly_extracted   : Output from extract_entities()
        allow_overwrite   : If True, apply source-priority rules. Defaults to False.
        existing_sources  : Source dict for the existing fields (may be None for
                            legacy sessions that predate source tracking).
        new_sources       : Source dict from extract_entities() for newly_extracted.

    Returns:
        (merged_fields, merged_sources) — both dicts updated in tandem.

    Example — THE BUG THIS FIXES:
        Turn 1 (LLM, allow_overwrite=False at /start):
            existing  = {}
            new       = {"plaintiff_name": "John Doe slipped"}   source: llm (1)
            → fills field (was empty)

        Turn 2 (regex, allow_overwrite=True at /provide):
            existing  = {"plaintiff_name": "John Doe slipped"}   source: llm (1)
            new       = {"plaintiff_name": "John Doe"}           source: regex (2)
            → regex (2) > llm (1) → REPLACE ✅
            → "John Doe slipped" is fixed to "John Doe"

        Turn 3 (human, allow_overwrite=True at /provide):
            existing  = {"plaintiff_name": "John Doe"}           source: regex (2)
            new       = {"plaintiff_name": "John M. Doe"}        source: human (3)
            → human (3) > regex (2) → REPLACE ✅

        Turn 4 (LLM again, allow_overwrite=True):
            existing  = {"plaintiff_name": "John M. Doe"}        source: human (3)
            new       = {"plaintiff_name": "Doe"}                source: llm (1)
            → llm (1) < human (3) → BLOCKED ✅  attorney value preserved
    """
    from utils import FIELD_SOURCE_PRIORITY

    merged         = dict(existing)
    merged_sources = dict(existing_sources or {})

    # Normalise inputs — default to empty dicts if None
    new_sources = new_sources or {}

    added   = []
    updated = []

    for key, val in newly_extracted.items():
        if key.startswith("__"):
            continue   # skip internal metadata keys

        existing_val = merged.get(key, "")

        if not existing_val:
            # Field is empty — always fill it regardless of overwrite flag
            merged[key]         = val
            merged_sources[key] = new_sources.get(key, "llm")
            added.append(key)
            continue

        if not allow_overwrite:
            # Strict mode (/intake/start) — never touch an existing value
            continue

        # ── Source-priority comparison ─────────────────────────────────────────
        # Existing flat fields with no recorded source are treated as "human" (3):
        # manual attorney input is always assumed to be trustworthy unless the
        # session explicitly tracked a lower-tier source.
        old_source = merged_sources.get(key, "human")
        new_source = new_sources.get(key, "llm")

        old_priority = FIELD_SOURCE_PRIORITY.get(old_source, 3)
        new_priority = FIELD_SOURCE_PRIORITY.get(new_source, 1)

        if new_priority > old_priority:
            # New source is more trusted — replace unconditionally.
            # This is the core bug fix: regex (2) replaces llm (1) even when
            # the regex value is shorter than the noisy LLM output.
            merged[key]         = val
            merged_sources[key] = new_source
            updated.append(key)

        elif new_priority == old_priority:
            # Same trust tier — prefer the shorter (cleaner) value.
            # Both came from the same type of source so neither is authoritative;
            # shorter usually means less context bleed.
            if len(str(val).strip()) < len(str(existing_val).strip()):
                merged[key]         = val
                merged_sources[key] = new_source
                updated.append(key)

        # new_priority < old_priority → do nothing; existing value is more trusted.

    if added:
        logger.debug(f"Merged {len(added)} new field(s) into session: {added}")
    if updated:
        logger.debug(
            f"Updated {len(updated)} field(s) via source-priority: {updated}"
        )

    return merged, merged_sources