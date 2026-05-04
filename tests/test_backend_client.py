"""
Tests for backend_client.py — all HTTP calls to the FastAPI backend.

Uses unittest.mock to patch requests.post/patch so no real backend is needed.
"""
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backend_client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_json_response(payload: dict, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.status_code = status_code
    mock_resp.raise_for_status.return_value = None
    return mock_resp

def _mock_bytes_response(content: bytes, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.content = content
    mock_resp.status_code = status_code
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ── ask_question ──────────────────────────────────────────────────────────────

MOCK_QUESTION_RESP = {
    "answer": "Legal analysis here.",
    "session_id": "sess-abc-123",
    "offer_complaint": True,
    "case_type": "personal_injury",
    "required_elements": [{"id": "plaintiff_name", "label": "Plaintiff Name"}],
    "sections": {},
    "classification_low_confidence": False,
}

def test_ask_question_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("My client was injured in NYC")
        url = mock_post.call_args[0][0]
        assert url.endswith("/questions")

def test_ask_question_sends_question_in_body():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("My client was injured in NYC")
        body = mock_post.call_args[1]["json"]
        assert body["question"] == "My client was injured in NYC"

def test_ask_question_sends_session_id_when_provided():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("follow up", session_id="existing-sess")
        body = mock_post.call_args[1]["json"]
        assert body["session_id"] == "existing-sess"

def test_ask_question_omits_session_id_when_none():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("first question", session_id=None)
        body = mock_post.call_args[1]["json"]
        assert "session_id" not in body

def test_ask_question_returns_parsed_dict():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        result = backend_client.ask_question("My client was injured in NYC")
        assert result["session_id"] == "sess-abc-123"
        assert result["offer_complaint"] is True
        assert result["case_type"] == "personal_injury"

def test_ask_question_raises_on_connection_error():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        with pytest.raises(requests.exceptions.ConnectionError):
            backend_client.ask_question("test")

def test_ask_question_raises_on_http_error():
    with patch("backend_client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("503")
        mock_post.return_value = mock_resp
        with pytest.raises(requests.exceptions.HTTPError):
            backend_client.ask_question("test")


# ── start_intake ──────────────────────────────────────────────────────────────

MOCK_INTAKE_START_RESP = {
    "case_id": "case-xyz-456",
    "case_type": "personal_injury",
    "is_complete": False,
    "pre_filled": {"plaintiff_name": "John Doe"},
    "missing_required": [{"id": "defendant_name", "label": "Defendant Name", "description": "...", "section": "Parties"}],
    "missing_questions": ["Who is the defendant?"],
    "missing_optional": [],
    "sections_display": {},
    "validation": {"is_valid": False, "can_draft": False, "issues": [], "sol_warning": None},
}

def test_start_intake_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.start_intake("sess-1", "personal_injury", "John Doe was injured")
        url = mock_post.call_args[0][0]
        assert url.endswith("/intake/start")

def test_start_intake_sends_correct_body():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.start_intake("sess-1", "personal_injury", "John Doe was injured")
        body = mock_post.call_args[1]["json"]
        assert body["session_id"] == "sess-1"
        assert body["case_type"] == "personal_injury"
        assert body["initial_text"] == "John Doe was injured"

def test_start_intake_returns_case_id():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        result = backend_client.start_intake("sess-1", "personal_injury", "text")
        assert result["case_id"] == "case-xyz-456"


# ── provide_intake ────────────────────────────────────────────────────────────

def test_provide_intake_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.provide_intake("case-xyz-456", "The defendant is Acme Corp")
        url = mock_post.call_args[0][0]
        assert "case-xyz-456/provide" in url

def test_provide_intake_sends_text_in_body():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.provide_intake("case-xyz-456", "The defendant is Acme Corp")
        body = mock_post.call_args[1]["json"]
        assert body["text"] == "The defendant is Acme Corp"


# ── force_draft ───────────────────────────────────────────────────────────────

def test_force_draft_uses_patch_method():
    with patch("backend_client.requests.patch") as mock_patch:
        mock_patch.return_value = _mock_json_response({"case_id": "case-xyz", "force_draft": True})
        backend_client.force_draft("case-xyz-456")
        url = mock_patch.call_args[0][0]
        assert "case-xyz-456/force" in url


# ── generate_draft ────────────────────────────────────────────────────────────

MOCK_DRAFT_RESP = {
    "case_id": "case-xyz-456",
    "case_type": "personal_injury",
    "complaint": "IN THE UNITED STATES DISTRICT COURT...",
    "from_cache": False,
    "word_count": 900,
    "unknown_count": 0,
}

def test_generate_draft_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_DRAFT_RESP)
        backend_client.generate_draft("case-xyz-456")
        url = mock_post.call_args[0][0]
        assert url.endswith("/draft/case-xyz-456")

def test_generate_draft_returns_complaint_text():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_DRAFT_RESP)
        result = backend_client.generate_draft("case-xyz-456")
        assert result["complaint"] == "IN THE UNITED STATES DISTRICT COURT..."
        assert result["word_count"] == 900


# ── download_docx ─────────────────────────────────────────────────────────────

def test_download_docx_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"PK\x03\x04fakeDOCX")
        backend_client.download_docx("case-xyz-456")
        url = mock_post.call_args[0][0]
        assert url.endswith("/document/case-xyz-456")
        assert "pdf" not in url

def test_download_docx_returns_bytes():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"PK\x03\x04fakeDOCX")
        result = backend_client.download_docx("case-xyz-456")
        assert isinstance(result, bytes)


# ── download_pdf ──────────────────────────────────────────────────────────────

def test_download_pdf_posts_to_pdf_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"%PDF-1.4 fakePDF")
        backend_client.download_pdf("case-xyz-456")
        url = mock_post.call_args[0][0]
        assert url.endswith("/document/case-xyz-456/pdf")

def test_download_pdf_returns_bytes():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"%PDF-1.4 fakePDF")
        result = backend_client.download_pdf("case-xyz-456")
        assert isinstance(result, bytes)
