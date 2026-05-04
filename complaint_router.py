"""
complaint_router.py — Complaint Drafting Endpoints (Day 7)
------------------------------------------------------------
Exposes two endpoints:

    POST /draft/{case_id}
        → Validates the case, calls Claude, returns the complaint text.
          If the draft already exists (draft lock), returns the cached version.

    GET  /draft/{case_id}
        → Returns the cached draft if one exists, 404 if not yet generated.

Both endpoints load the full case session via get_case_session() from app.py
so no DB logic is duplicated here.
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from complaint_drafter import draft_complaint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/draft", tags=["complaint"])


# ──────────────────────────────────────────────
# Response Models
# ──────────────────────────────────────────────
class DraftResponse(BaseModel):
    case_id:       str
    case_type:     str
    complaint:     str
    from_cache:    bool
    word_count:    int
    unknown_count: int   # number of [UNKNOWN] placeholders remaining in the document


# ──────────────────────────────────────────────
# Shared session loader
# Imported from app.py at request time (not at module load) to avoid
# circular imports — app.py imports this router, this router needs app.py helpers.
# ──────────────────────────────────────────────
def _load_session(case_id: str) -> dict:
    """Load and return the case session dict via app.get_case_session()."""
    from app import get_case_session
    session = get_case_session(case_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Case session '{case_id}' not found."
        )
    return session


# ──────────────────────────────────────────────
# POST /draft/{case_id}
# ──────────────────────────────────────────────
@router.post("/{case_id}", response_model=DraftResponse)
async def generate_draft(case_id: str):
    """
    Generate a formal legal complaint for the given case session.

    Flow:
      1. Load the case session (fields, validation state, draft lock).
      2. If draft_generated=True, return cached complaint immediately.
      3. Check can_draft — if False and force_draft=False, return 422
         with the list of issues the attorney needs to resolve.
      4. Call Claude via complaint_drafter.draft_complaint().
      5. Return the complaint text with metadata.

    Response (HTTP 200):
        {
            "case_id":       "...",
            "case_type":     "personal_injury",
            "complaint":     "IN THE UNITED STATES DISTRICT COURT...",
            "from_cache":    false,
            "word_count":    912,
            "unknown_count": 2
        }

    Error responses:
        503 — Claude API unavailable
        500 — unexpected drafting error
    Note:
        Drafting is never blocked by missing fields. If fields are absent,
        the complaint is generated with [UNKNOWN] placeholders and the
        response unknown_count will be non-zero.
    """
    session = _load_session(case_id)

    force_draft     = bool(session.get("force_draft", False))
    draft_generated = bool(session.get("draft_generated", False))
    draft_text      = session.get("draft_text")

    # ── Gate: refuse to draft when required fields are still missing ──────────
    # missing_fields is a list of field IDs already deserialized by get_case_session().
    missing_field_ids: list = session.get("missing_fields") or []

    if missing_field_ids and not force_draft:
        from intake_router import FIELD_QUESTIONS
        questions = [
            {
                "field_id": fid,
                "question": FIELD_QUESTIONS.get(
                    fid,
                    f"Please provide {fid.replace('_', ' ')}."
                ),
            }
            for fid in missing_field_ids
        ]
        logger.warning(
            f"POST /draft/{case_id} — blocked: {len(missing_field_ids)} required "
            f"field(s) missing: {missing_field_ids}"
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    f"Cannot generate complaint — {len(missing_field_ids)} required "
                    f"field(s) are missing. Please answer the questions below and submit "
                    f"each answer via POST /intake/{case_id}/provide, then retry."
                ),
                "missing_count": len(missing_field_ids),
                "fields": questions,
            },
        )

    can_draft = True

    try:
        result = draft_complaint(
            case_id         = case_id,
            provided_fields = session["provided_fields"],
            required_fields = session["required_fields"],
            case_type       = session["case_type"],
            force_draft     = force_draft,
            can_draft       = can_draft,
            draft_generated = draft_generated,
            draft_text      = draft_text,
        )
    except ValueError as e:
        # Validation gate inside draft_complaint fired (shouldn't normally happen
        # here since we check can_draft above, but belt-and-suspenders)
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        # Claude API failure
        logger.error(f"Claude API error for case_id={case_id}: {e}")
        raise HTTPException(
            status_code=503,
            detail=(
                f"Complaint drafting service unavailable: {e}. "
                f"Please try again in a moment."
            )
        )
    except Exception as e:
        logger.error(f"Unexpected drafting error for case_id={case_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred during complaint generation."
        )

    logger.info(
        f"POST /draft/{case_id} complete | "
        f"from_cache={result['from_cache']} | "
        f"words={result['word_count']} | "
        f"unknowns={result['unknown_count']}"
    )
    return result


# ──────────────────────────────────────────────
# GET /draft/{case_id}
# ──────────────────────────────────────────────
@router.get("/{case_id}", response_model=DraftResponse)
async def get_draft(case_id: str):
    """
    Return the cached complaint draft for a case session.

    Returns 404 if no draft has been generated yet for this case.
    The frontend uses this on page reload to restore a previously
    generated draft without triggering a new Claude API call.

    Response (HTTP 200):
        {
            "case_id":       "...",
            "case_type":     "personal_injury",
            "complaint":     "IN THE UNITED STATES DISTRICT COURT...",
            "from_cache":    true,
            "word_count":    912,
            "unknown_count": 2
        }
    """
    session         = _load_session(case_id)
    draft_generated = bool(session.get("draft_generated", False))
    draft_text      = session.get("draft_text")

    if not draft_generated or not draft_text:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No complaint draft found for case '{case_id}'. "
                f"Generate one via POST /draft/{case_id}."
            )
        )

    from complaint_drafter import _word_count, _count_unknowns

    logger.info(f"GET /draft/{case_id} — returning cached draft")
    return {
        "case_id":       case_id,
        "case_type":     session["case_type"],
        "complaint":     draft_text,
        "from_cache":    True,
        "word_count":    _word_count(draft_text),
        "unknown_count": _count_unknowns(draft_text),
    }