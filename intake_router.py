"""
intake_router.py — Case Intake Loop (Day 5 + Day 6)
------------------------------------------------------
Implements the smart, stateful intake loop that:

  1. Accepts the attorney's initial description of the case
  2. Runs entity_extractor to auto-fill every field it can detect from prose
  3. Validates all collected fields via validator.py (Day 6)
  4. Returns ONLY the fields that are still genuinely missing
  5. Persists state (including validation result) in the case_sessions table
  6. Accepts follow-up messages and merges newly extracted fields each time
  7. Re-validates after every intake turn so the attorney gets live feedback
  8. Marks the intake as complete when all required fields are filled

Why this matters:
    Without this module, the system would ask the attorney for plaintiff_name,
    defendant_name, incident_date and incident_location even when they wrote:
        "My client John Doe was rear-ended by Mark Smith on Jan 5 on Sunset Blvd."
    With this module those four fields are auto-filled immediately, and the
    attorney is only asked for what is genuinely still missing.

Day 6 additions:
  - validator.py integrated — runs after every extraction step
  - validation result persisted to case_sessions.validation_result
  - "validation" block included in every /start and /provide response
  - GET /validate/{case_id} endpoint for standalone validation checks

API surface:

    POST /intake/start
        Body : { session_id, case_type, initial_text }
        → Creates a new case_session, auto-fills from initial_text,
          validates, returns case_id + sectioned view + validation result.

    POST /intake/{case_id}/provide
        Body : { text }
        → Attorney sends more information. System extracts + merges + validates.
          Returns the updated sectioned view + validation result.

    GET  /intake/{case_id}
        → Returns the current state of a case_session (for UI reload).

    GET  /validate/{case_id}
        → Returns the latest validation result for a case session.
          Used by the frontend to check whether the case is ready to draft.

    PATCH /intake/{case_id}/force
        → Attorney acknowledges missing fields and proceeds to drafting.
          Returns missing_required so the UI can display what will be
          left as [UNKNOWN] in the generated complaint.

Response shape (same for start and provide):
    {
        "case_id":      "...",
        "case_type":    "personal_injury",
        "is_complete":  false,

        "pre_filled": {                       # fields auto-detected from text
            "injury_description": "broken arm",
            "incident_location":  "grocery store"
        },
        "missing_required": [                 # ONLY required fields still empty
            { "id": "plaintiff_name", "label": "Plaintiff Name",
              "description": "...", "section": "Parties" },
            ...
        ],
        "missing_optional": [                 # optional fields still empty
            { "id": "witness_names", "label": "Witness Names", ... },
            ...
        ],

        "sections_display": {                 # full sectioned view for UI rendering
            "Parties": {
                "fields": [
                    { "id": "plaintiff_name", "label": "Plaintiff Name",
                      "value": null, "filled": false, "required": true },
                    { "id": "defendant_name", "label": "Defendant Name",
                      "value": null, "filled": false, "required": true }
                ]
            },
            "Incident Details": {
                "fields": [
                    { "id": "incident_date",     "label": "Date of Incident",
                      "value": null,           "filled": false, "required": true },
                    { "id": "incident_location", "label": "Location",
                      "value": "grocery store","filled": true,  "required": true }
                ]
            },
            ...
        }
    }

Storage:
    Uses the case_sessions table (already created in app.py init_db).
    provided_fields and required_fields stored as JSON strings.
"""

import json
import logging
import uuid
from datetime import datetime
from contextlib import contextmanager

import sqlite3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from element_extractor import extract_elements, STATIC_ELEMENTS
from entity_extractor  import extract_entities, merge_provided_fields
from validator         import validate_case_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intake", tags=["intake"])

# Allowed case types — derived from the static schema.
# Any case_type not in this set will be rejected at /intake/start.
# This prevents a client sending an arbitrary string and triggering a model call.
ALLOWED_CASE_TYPES = [ct for ct in STATIC_ELEMENTS if ct != "other"]

# ──────────────────────────────────────────────
# DB helpers — identical pattern to app.py
# The router shares the same DB_PATH; importing DB_PATH from app would
# create a circular import so we read the env var here directly.
# ──────────────────────────────────────────────
import os
DB_PATH = os.getenv("DB_PATH", "chat_history.db")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Intake DB error: {e}")
        raise
    finally:
        conn.close()


# ──────────────────────────────────────────────
# Pydantic Request Models
# ──────────────────────────────────────────────
class IntakeStartRequest(BaseModel):
    session_id:   str
    case_type:    str
    initial_text: str

    @field_validator("session_id", "case_type", "initial_text")
    @classmethod
    def not_empty(cls, v: str, info) -> str:
        v = v.strip()
        if not v:
            raise ValueError(f"{info.field_name} cannot be empty.")
        return v


class IntakeProvideRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text cannot be empty.")
        return v


# ──────────────────────────────────────────────
# Internal Helpers
# ──────────────────────────────────────────────
def _compute_missing(
    elements:        list[dict],
    provided_fields: dict,
) -> tuple[list[dict], list[dict]]:
    """
    Split elements into (missing_required, missing_optional) based on what has
    been provided so far.

    A field is considered "provided" if its id exists in provided_fields AND
    the value is a non-empty string.

    Returns:
        missing_required : list of element dicts for required fields not yet filled
        missing_optional : list of element dicts for optional fields not yet filled
    """
    missing_required = []
    missing_optional = []

    for el in elements:
        fid   = el["id"]
        value = provided_fields.get(fid, "")
        if value and str(value).strip():
            continue  # field is filled — skip
        if el.get("required", False):
            missing_required.append(el)
        else:
            missing_optional.append(el)

    return missing_required, missing_optional


# ──────────────────────────────────────────────
# Smart question map — converts raw field IDs into natural attorney-facing
# questions. Covers every field ID used across all supported case types.
# If a field ID is not in this map the fallback formats the id into plain
# English (e.g. "insurance_policy_number" → "Please provide insurance policy number.").
# ──────────────────────────────────────────────
FIELD_QUESTIONS: dict[str, str] = {
    # ── Parties ───────────────────────────────
    "plaintiff_name":           "What is the plaintiff's full legal name?",
    "defendant_name":           "Who is the defendant? (full legal name or company name)",
    "defendant_address":        "What is the defendant's address?",
    "plaintiff_address":        "What is the plaintiff's address?",
    "defendant_employer":       "Who is the defendant's employer?",
    "plaintiff_employer":       "Who is the plaintiff's employer?",
    "guardian_name":            "What is the guardian's full legal name?",
    "insurance_company":        "What is the name of the insurance company involved?",
    "insurance_policy_number":  "What is the insurance policy number?",

    # ── Incident ──────────────────────────────
    "incident_date":            "On what date did the incident occur?",
    "incident_location":        "Where did the incident occur?",
    "incident_description":     "Can you describe what happened during the incident?",
    "negligence_act":           "What negligent act or omission caused the injury?",
    "property_address":         "What is the address of the property involved?",

    # ── Injury / Damages ──────────────────────
    "injury_description":       "What injuries did the plaintiff suffer?",
    "medical_treatment":        "What medical treatment did the plaintiff receive?",
    "medical_expenses":         "What are the total medical expenses incurred?",
    "lost_wages":               "How much in lost wages has the plaintiff suffered?",
    "damages_claimed":          "What is the total amount of damages being claimed?",
    "pain_suffering":           "Can you describe the plaintiff's pain and suffering?",
    "property_damage":          "What property damage occurred?",
    "repair_cost":              "What is the estimated repair cost?",

    # ── Employment ────────────────────────────
    "employer_name":            "What is the employer's full legal name?",
    "employment_start_date":    "When did the plaintiff's employment begin?",
    "termination_date":         "On what date was the plaintiff terminated?",
    "termination_reason":       "What reason was given for the termination?",
    "wages_owed":               "How much in unpaid wages is the plaintiff owed?",
    "job_title":                "What was the plaintiff's job title?",
    "discrimination_basis":     "On what basis does the plaintiff allege discrimination?",
    "harassment_description":   "Can you describe the harassment that occurred?",
    "retaliation_description":  "What retaliatory action did the employer take?",

    # ── Contract ──────────────────────────────
    "contract_date":            "On what date was the contract signed?",
    "contract_value":           "What is the total value of the contract?",
    "breach_description":       "How did the defendant breach the contract?",
    "contract_subject":         "What is the subject matter of the contract?",
    "performance_description":  "What performance was the plaintiff obligated to provide?",

    # ── Criminal / Defense ────────────────────
    "arrest_date":              "On what date was the defendant arrested?",
    "charges":                  "What criminal charges have been filed?",
    "arresting_agency":         "Which law enforcement agency made the arrest?",
    "bail_amount":              "What is the bail amount set by the court?",
    "plea":                     "What plea has the defendant entered?",

    # ── Property / Real Estate ────────────────
    "property_value":           "What is the fair market value of the property?",
    "taking_date":              "On what date did the taking or seizure occur?",
    "fair_market_value":        "What is the property's fair market value?",
    "compensation_offered":     "What compensation was offered by the government?",
    "property_description":     "Can you describe the property involved?",

    # ── Family Law ────────────────────────────
    "marriage_date":            "On what date did the parties marry?",
    "separation_date":          "On what date did the parties separate?",
    "children":                 "Are there any children from the marriage? If so, how many?",
    "custody_arrangement":      "What custody arrangement is being sought?",
    "asset_description":        "What marital assets need to be divided?",

    # ── Witnesses / Evidence ──────────────────
    "witness_names":            "Are there any witnesses? If so, what are their names?",
    "police_report_number":     "Is there a police report? If so, what is the report number?",
    "evidence_description":     "What evidence is available to support the claim?",

    # ── Court / Caption fields ────────────────
    # Optional — auto-detected from case type when not provided.
    "court_name":               "What court will this be filed in? (e.g. United States District Court, Superior Court)",
    "court_district":           "What is the court district? (e.g. Central District, Northern District)",
    "court_state":              "In which state will this complaint be filed?",
    "case_number":              "Has a case number been assigned? (Leave blank if not yet assigned)",
}


def _field_to_question(field_id: str) -> str:
    """
    Convert a raw field ID into a natural attorney-facing question.

    Uses FIELD_QUESTIONS for known fields. Falls back to formatting
    the field ID as plain English for any unknown fields — so this
    function is safe to call with any string and never returns None.

    Example:
        "incident_location"      → "Where did the incident occur?"
        "some_new_custom_field"  → "Please provide some new custom field."
    """
    return FIELD_QUESTIONS.get(
        field_id,
        f"Please provide {field_id.replace('_', ' ')}."
    )


def _build_sections_display(
    elements:        list[dict],
    sections:        dict,
    provided_fields: dict,
) -> dict:
    """
    Build the rich sectioned display structure for the frontend.

    Each section contains a list of field objects with:
        id, label, description, required, value (or null), filled (bool)

    Within each section, required fields are sorted BEFORE optional fields.
    Within each group (required / optional), the original schema order is preserved.
    This ensures the attorney sees the most important fields first in every section,
    regardless of how the schema happens to order them.

    The frontend iterates this to render:
        ✔ filled fields  (green / pre-populated)
        □ missing fields (empty input boxes, required always above optional)

    Args:
        elements        : Flat list from extract_elements()
        sections        : Ordered dict of section_name → [field_id, ...]
        provided_fields : Current provided_fields from case_session

    Returns:
        { section_name: { "fields": [ {...}, ... ] } }
    """
    # Build a lookup so we can access element metadata by id in O(1)
    element_by_id = {el["id"]: el for el in elements}

    display = {}
    for section_name, field_ids in sections.items():
        required_fields = []
        optional_fields = []

        for fid in field_ids:
            el = element_by_id.get(fid)
            if not el:
                continue
            value  = provided_fields.get(fid, None)
            filled = bool(value and str(value).strip())
            entry  = {
                "id":          fid,
                "label":       el["label"],
                "description": el["description"],
                "required":    el.get("required", False),
                "value":       str(value).strip() if filled else None,
                "filled":      filled,
            }
            # Sort required before optional within the section
            if el.get("required", False):
                required_fields.append(entry)
            else:
                optional_fields.append(entry)

        # Filled fields float to the top of their group for a cleaner UI
        fields = (
            sorted(required_fields, key=lambda f: (not f["filled"]))  # filled required first
            + sorted(optional_fields, key=lambda f: (not f["filled"]))  # filled optional after
        )

        if fields:
            display[section_name] = {"fields": fields}

    return display


def _build_intake_response(
    case_id:          str,
    case_type:        str,
    elements:         list[dict],
    sections:         dict,
    provided_fields:  dict,
    newly_filled:     dict,
    validation:       dict,
) -> dict:
    """
    Build the standard intake response dict returned by both /start and /provide.

    Args:
        case_id         : UUID of the case_session
        case_type       : e.g. "personal_injury"
        elements        : Flat element list from extract_elements()
        sections        : Ordered section → [field_id] dict
        provided_fields : All fields collected so far (cumulative)
        newly_filled    : Only the fields extracted in THIS turn (for the pre_filled display)
        validation      : Full validation result dict from validate_case_fields()

    Returns:
        The full response dict (serialised to JSON by FastAPI automatically).
    """
    missing_required, missing_optional = _compute_missing(elements, provided_fields)
    is_complete = len(missing_required) == 0

    return {
        "case_id":          case_id,
        "case_type":        case_type,
        "is_complete":      is_complete,
        "pre_filled":       newly_filled,
        "missing_required": [
            {
                "id":          el["id"],
                "label":       el["label"],
                "description": el["description"],
                "section":     el.get("section", ""),
            }
            for el in missing_required
        ],
        # Human-readable questions for each missing required field.
        # Use these instead of raw field IDs when prompting the attorney.
        # Example: "injury_description" → "What injuries did the plaintiff suffer?"
        "missing_questions": [
            _field_to_question(el["id"])
            for el in missing_required
        ],
        "missing_optional": [
            {
                "id":          el["id"],
                "label":       el["label"],
                "description": el["description"],
                "section":     el.get("section", ""),
            }
            for el in missing_optional
        ],
        "sections_display": _build_sections_display(elements, sections, provided_fields),
        # Day 6 — validation result included in every response so the UI
        # can show live warnings and the "Generate Complaint" button state.
        "validation": {
            "is_valid":             validation["is_valid"],
            "can_draft":            validation["can_draft"],
            "issues":               validation["issues"],
            "sol_warning":          validation["sol_warning"],
            "validation_summary":   validation.get("validation_summary", ""),
            # Weighted progress indicator (0–100).
            # Required fields count double optional fields.
            # Use for frontend progress bar: "Complaint readiness: 72%"
            "draft_readiness_score": validation.get("draft_readiness_score", 0),
        },
    }


# ──────────────────────────────────────────────
# POST /intake/start
# ──────────────────────────────────────────────
@router.post("/start")
async def intake_start(request: IntakeStartRequest):
    """
    Start a new case intake session.

    Flow:
      1. Fetch required elements + sections for the case type.
      2. Run entity_extractor on initial_text — auto-fill what we can.
      3. Create a case_session row in the DB.
      4. Return the full intake response: pre-filled fields + missing fields
         grouped by section.

    The frontend should:
      - Show a ✔ next to every pre_filled field
      - Show an input box for every field in missing_required
      - Show optional fields collapsed or greyed out
    """
    req_id = str(uuid.uuid4())[:8]   # short 8-char ID — enough to correlate logs
    logger.info(f"[{req_id}] POST /intake/start | session={request.session_id} | case_type={request.case_type}")

    # Validate case_type before doing anything else — no DB write, no GPU call.
    # Guards against frontend bugs or malicious clients sending arbitrary strings.
    # "other" is intentionally excluded: it has no element schema so a complaint
    # cannot be drafted for it.
    if request.case_type not in ALLOWED_CASE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid case_type '{request.case_type}'. "
                f"Supported types: {', '.join(ALLOWED_CASE_TYPES)}"
            )
        )

    # ── Input length guard ────────────────────────────────────────────────────
    if len(request.initial_text) > 5000:
        raise HTTPException(
            status_code=413,
            detail=(
                "Input text is too long "
                f"({len(request.initial_text)} chars). "
                "Maximum allowed is 5,000 characters per intake submission."
            )
        )

    # ── Step 1: Get elements + sections ──────────────────────────────────────
    result   = extract_elements(request.case_type, None, None)
    elements = result["elements"]
    sections = result["sections"]

    if not elements:
        raise HTTPException(
            status_code=422,
            detail=f"No legal elements found for case type '{request.case_type}'. "
                   f"Only supported case types can be used for intake."
        )

    # all_field_ids includes both required AND optional fields.
    # extract_entities needs all of them so it can auto-fill optional fields
    # (e.g. witness_names, police_report_number) when mentioned in prose.
    # The name "all_field_ids" distinguishes this from required-only lists
    # used elsewhere in the pipeline.
    all_field_ids = [el["id"] for el in elements]

    # ── Step 2: Auto-fill from initial_text ──────────────────────────────────
    extracted, extracted_sources = extract_entities(
        text                 = request.initial_text,
        case_type            = request.case_type,
        required_element_ids = all_field_ids,
    )
    provided_fields = dict(extracted)  # start fresh from extraction

    logger.info(
        f"[{req_id}] Intake start: auto-filled {len(provided_fields)} / {len(all_field_ids)} fields "
        f"from initial text."
    )

    # ── Step 3: Persist to case_sessions ─────────────────────────────────────
    case_id = str(uuid.uuid4())
    now     = datetime.utcnow().isoformat()

    missing_required, _ = _compute_missing(elements, provided_fields)
    missing_field_ids   = [el["id"] for el in missing_required]

    # Embed source-tracking metadata inside the JSON blob so it travels with
    # provided_fields across DB reads/writes without a schema change.
    # The "__sources__" key is stripped by every consumer before use —
    # only intake_router reads and writes it.
    provided_fields_with_meta = {**provided_fields, "__sources__": extracted_sources}

    with get_db() as conn:
        # Verify the parent chat session exists (soft guard — don't crash if not)
        row = conn.execute(
            "SELECT id FROM sessions WHERE id = ?", (request.session_id,)
        ).fetchone()
        if not row:
            logger.warning(
                f"Chat session '{request.session_id}' not found — "
                f"creating case_session anyway (foreign key unenforced in SQLite)."
            )

        conn.execute(
            """
            INSERT INTO case_sessions
              (case_id, chat_session_id, case_type,
               required_fields, provided_fields, missing_fields,
               force_draft, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                case_id,
                request.session_id,
                request.case_type,
                json.dumps(all_field_ids),               # stores all field IDs (req + optional)
                json.dumps(provided_fields_with_meta),   # includes __sources__
                json.dumps(missing_field_ids),
                now, now,
            ),
        )

    logger.info(
        f"[{req_id}] Intake case_session created | case_id={case_id} | "
        f"filled={len(provided_fields)} | missing_required={len(missing_field_ids)}"
    )

    # ── Step 4: Validate collected fields ────────────────────────────────────
    # Runs immediately after extraction so the attorney sees field-level
    # warnings on the very first turn — not just after they've filled everything.
    validation = validate_case_fields(
        case_type       = request.case_type,
        provided_fields = provided_fields,
        elements        = elements,
        force_draft     = False,   # force_draft is always False at start
    )

    # Persist validation result to DB
    with get_db() as conn:
        conn.execute(
            "UPDATE case_sessions SET validation_result = ?, updated_at = ? WHERE case_id = ?",
            (json.dumps(validation), datetime.utcnow().isoformat(), case_id),
        )

    logger.info(
        f"[{req_id}] Intake start validated | case_id={case_id} | "
        f"is_valid={validation['is_valid']} | issues={len(validation['issues'])}"
    )

    return _build_intake_response(
        case_id         = case_id,
        case_type       = request.case_type,
        elements        = elements,
        sections        = sections,
        provided_fields = provided_fields,
        newly_filled    = extracted,   # first turn: all extracted fields are "newly filled"
        validation      = validation,
    )


# ──────────────────────────────────────────────
# POST /intake/{case_id}/provide
# ──────────────────────────────────────────────
@router.post("/{case_id}/provide")
async def intake_provide(case_id: str, request: IntakeProvideRequest):
    """
    Process a follow-up message from the attorney during intake.

    Flow:
      1. Load the case_session from DB.
      2. Run entity_extractor on the new text.
      3. Merge newly extracted fields into provided_fields (existing values win).
      4. Recompute missing fields.
      5. Save updated state to DB.
      6. Return the full intake response with the new state.

    The attorney can send natural prose like:
        "The plaintiff is Jane Smith. The accident happened on March 3rd."
    And the system will extract plaintiff_name and incident_date automatically,
    update missing fields, and only ask for what is still genuinely absent.
    """
    req_id = str(uuid.uuid4())[:8]
    logger.info(f"[{req_id}] POST /intake/{case_id}/provide | text_len={len(request.text)}")

    # ── Input length guard ────────────────────────────────────────────────────
    if len(request.text) > 5000:
        raise HTTPException(
            status_code=413,
            detail=(
                "Input text is too long "
                f"({len(request.text)} chars). "
                "Maximum allowed is 5,000 characters per intake submission."
            )
        )

    # ── Step 1: Load case_session ─────────────────────────────────────────────
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM case_sessions WHERE case_id = ?", (case_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Case session '{case_id}' not found.")

    case_type       = row["case_type"]
    provided_fields = json.loads(row["provided_fields"])
    required_ids    = json.loads(row["required_fields"])

    # ── Unpack source metadata from stored provided_fields ────────────────────
    # __sources__ is embedded in the JSON blob at /intake/start.
    # Pop it out here so the plain field dict can be passed safely to all
    # consumers (validator, _compute_missing, etc.) that expect flat strings.
    current_sources: dict = provided_fields.pop("__sources__", {})

    # ── Step 2: Re-fetch elements + sections (from static schema — free) ──────
    result   = extract_elements(case_type, None, None)
    elements = result["elements"]
    sections = result["sections"]

    if not elements:
        raise HTTPException(
            status_code=500,
            detail=f"Schema missing for case type '{case_type}'. Cannot process intake."
        )

    # ── Step 3: Extract from new text ─────────────────────────────────────────
    newly_extracted, new_sources = extract_entities(
        text                 = request.text,
        case_type            = case_type,
        required_element_ids = required_ids,
    )

    if newly_extracted:
        logger.info(
            f"[{req_id}] Intake provide: extracted {len(newly_extracted)} field(s) from text"
        )
    else:
        logger.debug(
            f"[{req_id}] Intake provide: no entities detected in this turn — "
            f"text may be a correction, confirmation, or non-extractable prose"
        )

    # Merge — allow_overwrite=True so the attorney can correct or expand fields.
    # Source-priority logic inside merge_provided_fields handles which value wins:
    #   regex (2) replaces llm (1)  even when the regex value is shorter   ← BUG FIX
    #   human (3) replaces regex (2) always                                ← attorney is authoritative
    #   llm (1) never replaces human (3) or regex (2)                      ← LLM cannot undo corrections
    updated_fields, updated_sources = merge_provided_fields(
        existing         = provided_fields,
        newly_extracted  = newly_extracted,
        allow_overwrite  = True,
        existing_sources = current_sources,
        new_sources      = new_sources,
    )

    # ── Step 4: Recompute missing ──────────────────────────────────────────────
    missing_required, _ = _compute_missing(elements, updated_fields)
    missing_field_ids   = [el["id"] for el in missing_required]

    # ── Step 5: Persist updated state ─────────────────────────────────────────
    now = datetime.utcnow().isoformat()
    # Re-embed source metadata before saving so the next /provide call can load it.
    updated_fields_with_meta = {**updated_fields, "__sources__": updated_sources}
    with get_db() as conn:
        conn.execute(
            """
            UPDATE case_sessions
            SET provided_fields = ?, missing_fields = ?, updated_at = ?
            WHERE case_id = ?
            """,
            (
                json.dumps(updated_fields_with_meta),   # includes __sources__
                json.dumps(missing_field_ids),
                now,
                case_id,
            ),
        )

    logger.info(
        f"[{req_id}] Intake provide done | case_id={case_id} | "
        f"missing_required={len(missing_field_ids)}"
    )

    # ── Step 6: Validate updated fields ───────────────────────────────────────
    force_draft = bool(row["force_draft"])
    validation  = validate_case_fields(
        case_type       = case_type,
        provided_fields = updated_fields,
        elements        = elements,
        force_draft     = force_draft,
    )

    # Persist updated validation result
    with get_db() as conn:
        conn.execute(
            "UPDATE case_sessions SET validation_result = ?, updated_at = ? WHERE case_id = ?",
            (json.dumps(validation), datetime.utcnow().isoformat(), case_id),
        )

    logger.info(
        f"[{req_id}] Intake provide validated | case_id={case_id} | "
        f"is_valid={validation['is_valid']} | can_draft={validation['can_draft']} | "
        f"issues={len(validation['issues'])}"
    )

    return _build_intake_response(
        case_id         = case_id,
        case_type       = case_type,
        elements        = elements,
        sections        = sections,
        provided_fields = updated_fields,
        newly_filled    = newly_extracted,  # only fields found in THIS message
        validation      = validation,
    )


# ──────────────────────────────────────────────
# GET /validate/{case_id}  (Day 6)
# ──────────────────────────────────────────────
@router.get("/validate/{case_id}")
async def validate_case(case_id: str):
    """
    Return the latest validation result for a case session.

    The frontend calls this endpoint to decide whether to enable the
    "Generate Complaint" button. The drafting engine (Day 7) calls this
    before generating to double-check can_draft is True.

    If no validation has been run yet (session was just created and
    no /provide call has been made), runs validation on the spot.

    Response:
        {
            "case_id":          "...",
            "case_type":        "personal_injury",
            "is_valid":         false,
            "can_draft":        false,
            "missing_required": ["defendant_name"],
            "missing_optional": ["witness_names"],
            "issues": [
                { "field": "plaintiff_name",
                  "severity": "error",
                  "message": "..." }
            ],
            "sol_warning": null
        }
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM case_sessions WHERE case_id = ?", (case_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Case session '{case_id}' not found.")

    case_type       = row["case_type"]
    provided_fields = json.loads(row["provided_fields"])
    provided_fields.pop("__sources__", None)   # strip metadata — not needed for validation
    force_draft     = bool(row["force_draft"])
    # Fresh computation is a safety net for sessions created before Day 6 was deployed.
    stored = row["validation_result"]
    if stored:
        try:
            validation = json.loads(stored)
            # Patch can_draft using the current force_draft flag
            # (force_draft may have been set after the last validation run)
            validation["can_draft"] = validation.get("is_valid", False) or force_draft
        except (json.JSONDecodeError, TypeError):
            validation = None

    if not stored or validation is None:
        result   = extract_elements(case_type, None, None)
        elements = result["elements"]
        validation = validate_case_fields(
            case_type       = case_type,
            provided_fields = provided_fields,
            elements        = elements,
            force_draft     = force_draft,
        )
        # Persist the freshly computed result
        with get_db() as conn:
            conn.execute(
                "UPDATE case_sessions SET validation_result = ?, updated_at = ? WHERE case_id = ?",
                (json.dumps(validation), datetime.utcnow().isoformat(), case_id),
            )

    logger.info(
        f"GET /validate/{case_id} | is_valid={validation.get('is_valid')} | "
        f"can_draft={validation.get('can_draft')}"
    )

    return {
        "case_id":                case_id,
        "case_type":              case_type,
        "is_valid":               validation.get("is_valid", False),
        "can_draft":              validation.get("can_draft", False),
        "missing_required":       validation.get("missing_required", []),
        "missing_optional":       validation.get("missing_optional", []),
        "issues":                 validation.get("issues", []),
        "sol_warning":            validation.get("sol_warning"),
        "validation_summary":     validation.get("validation_summary", ""),
        # Weighted progress indicator (0–100). Required fields count double optional.
        # Use for frontend progress bar: "Complaint readiness: 72%"
        "draft_readiness_score":  validation.get("draft_readiness_score", 0),
    }


# ──────────────────────────────────────────────
# GET /intake/{case_id}
# ──────────────────────────────────────────────
@router.get("/{case_id}")
async def intake_get(case_id: str):
    """
    Return the current state of a case_session.

    Used by the frontend to restore intake state on page reload or
    to check whether an intake session is already in progress.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM case_sessions WHERE case_id = ?", (case_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Case session '{case_id}' not found.")

    case_type       = row["case_type"]
    provided_fields = json.loads(row["provided_fields"])
    provided_fields.pop("__sources__", None)   # strip metadata — keep API response clean
    required_ids    = json.loads(row["required_fields"])

    result   = extract_elements(case_type, None, None)
    elements = result["elements"]
    sections = result["sections"]

    if not elements:
        raise HTTPException(
            status_code=500,
            detail=f"Schema missing for case type '{case_type}'. Cannot load intake state."
        )

    missing_required, missing_optional = _compute_missing(elements, provided_fields)

    # is_complete is true if all required fields are filled OR force_draft is set
    is_complete = len(missing_required) == 0 or bool(row["force_draft"])

    # Include the stored validation result if available
    stored_validation = None
    if row["validation_result"]:
        try:
            stored_validation = json.loads(row["validation_result"])
        except (json.JSONDecodeError, TypeError):
            stored_validation = None

    return {
        "case_id":          case_id,
        "case_type":        case_type,
        "chat_session_id":  row["chat_session_id"],
        "is_complete":      is_complete,
        "force_draft":      bool(row["force_draft"]),
        "provided_fields":  provided_fields,
        "missing_required": [
            {"id": el["id"], "label": el["label"], "section": el.get("section", "")}
            for el in missing_required
        ],
        "missing_optional": [
            {"id": el["id"], "label": el["label"], "section": el.get("section", "")}
            for el in missing_optional
        ],
        "sections_display": _build_sections_display(elements, sections, provided_fields),
        "validation":       stored_validation,   # None if not yet validated
        "created_at":       row["created_at"],
        "updated_at":       row["updated_at"],
    }


# ──────────────────────────────────────────────
# PATCH /intake/{case_id}/force
# ──────────────────────────────────────────────
@router.patch("/{case_id}/force")
async def intake_force_draft(case_id: str):
    """
    Mark the intake as force_draft=1 so the complaint engine will proceed
    even though some required fields are still missing.

    The attorney explicitly acknowledges missing fields and chooses to proceed.
    The complaint template will leave those fields as [UNKNOWN] placeholders.

    Response includes the list of missing required fields so the UI can show
    the attorney exactly what will appear as [UNKNOWN] in the final complaint —
    they should not be surprised by missing content after clicking "proceed".
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM case_sessions WHERE case_id = ?", (case_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Case session '{case_id}' not found.")

        conn.execute(
            "UPDATE case_sessions SET force_draft = 1, updated_at = ? WHERE case_id = ?",
            (datetime.utcnow().isoformat(), case_id),
        )

    # Compute which required fields are still missing so the UI can warn the attorney
    case_type       = row["case_type"]
    provided_fields = json.loads(row["provided_fields"])
    provided_fields.pop("__sources__", None)   # strip metadata

    result   = extract_elements(case_type, None, None)
    elements = result["elements"]

    missing_required, _ = _compute_missing(elements, provided_fields)

    logger.info(
        f"Intake force_draft set | case_id={case_id} | "
        f"proceeding with {len(missing_required)} missing required field(s)"
    )

    return {
        "case_id":          case_id,
        "force_draft":      True,
        "message":          "Proceeding to draft with missing fields marked as [UNKNOWN].",
        "missing_required": [
            {
                "id":          el["id"],
                "label":       el["label"],
                "description": el["description"],
                "section":     el.get("section", ""),
            }
            for el in missing_required
        ],
        "missing_count": len(missing_required),
    }

# ──────────────────────────────────────────────
# GET /case/{case_id}/progress
# ──────────────────────────────────────────────
# Separate router prefix so the URL is /case/{case_id}/progress.
# Registered in app.py alongside the main intake_router.
# ──────────────────────────────────────────────
from fastapi import APIRouter as _APIRouter
progress_router = _APIRouter(prefix="/case", tags=["intake"])


@progress_router.get("/{case_id}/progress")
async def case_progress(case_id: str):
    """
    Return a simple completion progress summary for a case session.

    Counts how many required + optional fields have been filled versus
    the total number of fields in the schema for this case type.
    Lawyers use this to understand at a glance how far along intake is.

    Response:
        {
            "case_id":           "...",
            "fields_completed":  6,
            "fields_total":      10,
            "progress":          "60%",
            "required_completed": 4,
            "required_total":     7,
            "optional_completed": 2,
            "optional_total":     3,
            "missing_questions": [
                "Where did the incident occur?",
                "What injuries did the plaintiff suffer?",
                "What negligent act caused the injury?"
            ]
        }

    The missing_questions list contains human-readable prompts for every
    required field that is still empty — so a frontend can display them
    directly without any further transformation.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM case_sessions WHERE case_id = ?", (case_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Case session '{case_id}' not found.")

    case_type       = row["case_type"]
    provided_fields = json.loads(row["provided_fields"])
    provided_fields.pop("__sources__", None)   # strip metadata

    result   = extract_elements(case_type, None, None)
    elements = result["elements"]

    if not elements:
        raise HTTPException(
            status_code=500,
            detail=f"Schema missing for case type '{case_type}'. Cannot compute progress."
        )

    # Tally required vs optional, filled vs missing
    required_total    = 0
    required_filled   = 0
    optional_total    = 0
    optional_filled   = 0
    missing_required  = []

    for el in elements:
        fid    = el["id"]
        value  = provided_fields.get(fid, "")
        filled = bool(value and str(value).strip())

        if el.get("required", False):
            required_total += 1
            if filled:
                required_filled += 1
            else:
                missing_required.append(el)
        else:
            optional_total += 1
            if filled:
                optional_filled += 1

    fields_total     = required_total + optional_total
    fields_completed = required_filled + optional_filled
    progress_pct     = (
        round(fields_completed / fields_total * 100) if fields_total > 0 else 0
    )

    logger.info(
        f"GET /case/{case_id}/progress | "
        f"completed={fields_completed}/{fields_total} ({progress_pct}%)"
    )

    return {
        "case_id":            case_id,
        "fields_completed":   fields_completed,
        "fields_total":       fields_total,
        "progress":           f"{progress_pct}%",
        "required_completed": required_filled,
        "required_total":     required_total,
        "optional_completed": optional_filled,
        "optional_total":     optional_total,
        # Human-readable questions for every required field still missing.
        # Ready to display directly to the attorney — no transformation needed.
        "missing_questions":  [
            _field_to_question(el["id"])
            for el in missing_required
        ],
    }