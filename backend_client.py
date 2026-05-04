"""
backend_client.py — FastAPI Backend HTTP Client
-------------------------------------------------
Single place for all HTTP calls to the Nyaay AI FastAPI backend.
The Streamlit app imports functions from here — it never calls requests directly.

Configuration (via environment variables):
    BACKEND_URL : Base URL of the FastAPI server (default: http://localhost:9000)
    API_KEY     : Bearer token for protected endpoints (default: empty)

All functions raise requests.exceptions.HTTPError on non-2xx responses.
ConnectionError is propagated as-is so the caller can show a "backend unreachable" message.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()  # load .env so API_KEY and BACKEND_URL are available to Streamlit

BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:9000").rstrip("/")
_API_KEY:    str = os.getenv("API_KEY", "")


def get_sessions() -> list:
    """GET /sessions — list all past chat sessions."""
    try:
        resp = requests.get(f"{BACKEND_URL}/sessions", headers=_headers(), timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def get_session_history(session_id: str) -> list:
    """GET /history/{session_id} — load full message history for a session."""
    resp = requests.get(f"{BACKEND_URL}/history/{session_id}", headers=_headers(), timeout=5)
    resp.raise_for_status()
    return [{"role": m["role"], "content": m["content"]} for m in resp.json()]


def _headers() -> dict:
    """Build request headers, including Bearer token if API_KEY is configured."""
    h = {"Content-Type": "application/json"}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


# ──────────────────────────────────────────────
# Chat Q&A
# ──────────────────────────────────────────────

def ask_question(question: str, session_id: str | None = None) -> dict:
    """
    POST /questions — ask a legal question, get an IRAC analysis back.

    Args:
        question   : The attorney's question or case description.
        session_id : Existing chat session UUID, or None to start a new session.

    Returns:
        {
            "answer":            str,
            "session_id":        str,
            "offer_complaint":   bool,
            "case_type":         str,
            "required_elements": list[dict],
            "sections":          dict,
            "classification_low_confidence": bool
        }

    Raises:
        requests.exceptions.ConnectionError — backend is unreachable
        requests.exceptions.HTTPError       — non-2xx response (e.g. 503 model loading)
    """
    payload: dict = {"question": question}
    if session_id:
        payload["session_id"] = session_id

    resp = requests.post(
        f"{BACKEND_URL}/questions",
        json=payload,
        headers=_headers(),
        timeout=120,   # Claude API can take up to ~30s; 120s gives headroom
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# Intake loop
# ──────────────────────────────────────────────

def start_intake(session_id: str, case_type: str, initial_text: str) -> dict:
    """
    POST /intake/start — start a new case intake session.

    Args:
        session_id   : Chat session UUID (from ask_question response).
        case_type    : Case type string (e.g. "personal_injury").
        initial_text : The attorney's original question/description used to auto-fill fields.

    Returns:
        {
            "case_id":          str,
            "case_type":        str,
            "is_complete":      bool,
            "pre_filled":       dict,          # fields auto-extracted from initial_text
            "missing_required": list[dict],    # [{id, label, description, section}, ...]
            "missing_questions": list[str],    # human-readable prompts for missing fields
            "missing_optional": list[dict],
            "sections_display": dict,
            "validation":       dict
        }

    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/intake/start",
        json={
            "session_id":   session_id,
            "case_type":    case_type,
            "initial_text": initial_text,
        },
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def provide_intake(case_id: str, text: str) -> dict:
    """
    POST /intake/{case_id}/provide — send follow-up information for field extraction.

    Args:
        case_id : Case session UUID (from start_intake response).
        text    : Free-form prose. Entity extraction runs on this and merges into fields.

    Returns: Same shape as start_intake response.
    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/intake/{case_id}/provide",
        json={"text": text},
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def force_draft(case_id: str) -> dict:
    """
    PATCH /intake/{case_id}/force — acknowledge missing fields and proceed to draft.

    Sets force_draft=1 on the case session so the complaint engine will generate
    with [UNKNOWN] placeholders for any missing required fields.

    Returns:
        {
            "case_id":          str,
            "force_draft":      True,
            "missing_required": list[dict]    # fields that will be [UNKNOWN]
        }

    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.patch(
        f"{BACKEND_URL}/intake/{case_id}/force",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# Complaint drafting
# ──────────────────────────────────────────────

def generate_draft(case_id: str) -> dict:
    """
    POST /draft/{case_id} — generate a formal complaint via Claude API.

    If a draft already exists for this case_id, the cached version is returned
    immediately (no duplicate Claude API charge).

    Returns:
        {
            "case_id":       str,
            "case_type":     str,
            "complaint":     str,    # full complaint text
            "from_cache":    bool,
            "word_count":    int,
            "unknown_count": int     # [UNKNOWN] placeholders remaining
        }

    Raises:
        requests.exceptions.ConnectionError — backend unreachable
        requests.exceptions.HTTPError 503   — Claude API unavailable / timed out
        requests.exceptions.HTTPError 500   — unexpected server error
    """
    resp = requests.post(
        f"{BACKEND_URL}/draft/{case_id}",
        headers=_headers(),
        timeout=90,   # Claude Sonnet typically responds in 10–30s; 90s is safe margin
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# Document export
# ──────────────────────────────────────────────

def download_docx(case_id: str) -> bytes:
    """
    POST /document/{case_id} — generate and return DOCX bytes.

    Returns: Raw DOCX bytes (application/vnd.openxmlformats-officedocument...)
    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/document/{case_id}",
        json={},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def download_pdf(case_id: str) -> bytes:
    """
    POST /document/{case_id}/pdf — generate and return PDF bytes.

    Returns: Raw PDF bytes (application/pdf)
    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/document/{case_id}/pdf",
        json={},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content
