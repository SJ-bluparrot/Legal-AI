"""
docx_router.py — DOCX Document Download Endpoints (Day 8)
-----------------------------------------------------------
Exposes the document export layer of the Legal AI platform.

Endpoints:

    POST /document/{case_id}
        Generate a .docx from the stored complaint draft and return it
        as a downloadable file response.

        Body (optional):
            { "attorney_name": "Jane Smith, Esq." }

        Returns: application/vnd.openxmlformats-officedocument.wordprocessingml.document
        Filename: complaint_{case_id[:8]}.docx

    GET /document/{case_id}/status
        Check whether a complaint draft exists for this case and is ready
        to export — without actually generating the file.

        Returns:
            {
                "case_id":          "...",
                "draft_exists":     true,
                "case_type":        "personal_injury",
                "word_count":       842,
                "unknown_count":    2,
                "ready_to_export":  true
            }

Design decisions:
    - DOCX is generated on demand from the stored draft_text — it is not
      persisted to disk. This avoids stale files and keeps storage simple.
      Generation is fast (~200ms) so on-demand is appropriate.
    - The endpoint always regenerates the file, so changes to the complaint
      text (e.g. after editing draft_text directly in the DB) are always
      reflected in the downloaded file.
    - attorney_name is optional. If omitted, [ATTORNEY NAME] appears in the
      signature block — consistent with how the rest of the system handles
      missing fields.
"""

import logging
import re
from io import BytesIO

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from docx_generator import generate_complaint_docx
from pdf_generator   import generate_complaint_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/document", tags=["document"])


# ──────────────────────────────────────────────
# Request / response models
# ──────────────────────────────────────────────
class DocumentRequest(BaseModel):
    """
    Optional body for POST /document/{case_id}.

    attorney_name : Injected into the signature block of the generated DOCX.
                    Defaults to [ATTORNEY NAME] if not provided.
    """
    attorney_name: str = ""


# ──────────────────────────────────────────────
# Shared DB helper — imported pattern from app.py
# Using the same lazy-import approach to avoid circular imports.
# ──────────────────────────────────────────────
from db import get_db


def _load_case_session(case_id: str) -> dict:
    """Load a case_session document. Raises 404 if not found."""
    doc = get_db().case_sessions.find_one({"_id": case_id})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Case session '{case_id}' not found."
        )
    return doc


def _count_unknowns(text: str) -> int:
    return len(re.findall(r'\[UNKNOWN\]', text))


def _word_count(text: str) -> int:
    return len(text.split())


# ══════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════

@router.post("/{case_id}")
async def generate_document(case_id: str, request: DocumentRequest = None):
    """
    Generate and download the DOCX complaint for a case session.

    Loads the stored complaint draft from the database, converts it to a
    formatted Word document, and streams it as a downloadable file.

    The attorney_name field (optional in the request body) is injected into
    the signature block. If omitted, [ATTORNEY NAME] is used as a placeholder.

    Errors:
        404 — case session not found
        422 — no complaint draft has been generated yet for this case
              (call POST /draft/{case_id} first)
        500 — DOCX generation failed (Node.js error or missing dependency)
    """
    if request is None:
        request = DocumentRequest()

    row = _load_case_session(case_id)

    if not row["draft_generated"] or not row["draft_text"]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No complaint draft found for case '{case_id}'. "
                "Generate the complaint first via POST /draft/{case_id}, "
                "then call this endpoint to download the DOCX."
            )
        )

    case_type      = row["case_type"]
    complaint_text = row["draft_text"]
    attorney_name  = (request.attorney_name or "").strip()

    logger.info(
        f"POST /document/{case_id} | case_type={case_type} | "
        f"attorney={'provided' if attorney_name else 'not provided'}"
    )

    try:
        docx_bytes = generate_complaint_docx(
            complaint_text = complaint_text,
            case_id        = case_id,
            case_type      = case_type,
            attorney_name  = attorney_name,
        )
    except RuntimeError as e:
        logger.error(f"DOCX generation failed | case_id={case_id} | error={e}")
        raise HTTPException(
            status_code=500,
            detail=f"DOCX generation failed: {e}"
        )

    # Derive a safe filename from the case_type and case_id prefix
    safe_type = case_type.replace("_", "-")
    filename  = f"complaint_{safe_type}_{case_id[:8]}.docx"

    return StreamingResponse(
        content     = BytesIO(docx_bytes),
        media_type  = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers     = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length":      str(len(docx_bytes)),
        },
    )


@router.get("/{case_id}/status")
async def document_status(case_id: str):
    """
    Check whether a complaint draft exists and is ready to export as DOCX.

    Does NOT generate or return the file — use POST /document/{case_id} for that.

    Returns:
        {
            "case_id":         "abc-123",
            "draft_exists":    true,
            "case_type":       "personal_injury",
            "word_count":      842,
            "unknown_count":   2,
            "ready_to_export": true    // true when draft_exists is true
        }
    """
    row = _load_case_session(case_id)

    draft_exists = bool(row["draft_generated"] and row["draft_text"])
    draft_text   = row["draft_text"] or ""

    return {
        "case_id":         case_id,
        "draft_exists":    draft_exists,
        "case_type":       row["case_type"],
        "word_count":      _word_count(draft_text) if draft_exists else 0,
        "unknown_count":   _count_unknowns(draft_text) if draft_exists else 0,
        "ready_to_export": draft_exists,
    }


@router.post("/{case_id}/pdf")
async def generate_pdf_document(case_id: str, request: DocumentRequest = None):
    """
    Generate and download the PDF complaint for a case session.

    Identical to POST /document/{case_id} but returns a PDF instead of DOCX.
    Uses ReportLab (pure Python) — no Node.js required.

    Errors:
        404 — case session not found
        422 — no complaint draft has been generated yet
        500 — PDF generation failed
    """
    if request is None:
        request = DocumentRequest()

    row = _load_case_session(case_id)

    if not row["draft_generated"] or not row["draft_text"]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No complaint draft found for case '{case_id}'. "
                "Generate the complaint first via POST /draft/{case_id}, "
                "then call this endpoint to download the PDF."
            )
        )

    case_type      = row["case_type"]
    complaint_text = row["draft_text"]
    attorney_name  = (request.attorney_name or "").strip()

    # provided_fields is already a dict from MongoDB
    case_fields = dict(row.get("provided_fields") or {})
    case_fields.pop("__sources__", None)

    logger.info(
        f"POST /document/{case_id}/pdf | case_type={case_type} | "
        f"attorney={'provided' if attorney_name else 'not provided'}"
    )

    try:
        pdf_bytes = generate_complaint_pdf(
            complaint_text = complaint_text,
            case_id        = case_id,
            case_type      = case_type,
            attorney_name  = attorney_name,
            case_fields    = case_fields,
        )
    except RuntimeError as e:
        logger.error(f"PDF generation failed | case_id={case_id} | error={e}")
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {e}"
        )

    safe_type = case_type.replace("_", "-")
    filename  = f"complaint_{safe_type}_{case_id[:8]}.pdf"

    return StreamingResponse(
        content    = BytesIO(pdf_bytes),
        media_type = "application/pdf",
        headers    = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length":      str(len(pdf_bytes)),
        },
    )