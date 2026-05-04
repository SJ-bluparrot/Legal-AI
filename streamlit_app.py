"""
streamlit_app.py — Nyaay AI Chat Interface
-------------------------------------------
ChatGPT/Claude-style legal assistant for New York attorneys.

Flow:
  1. Attorney types a legal question or case description.
  2. NY filter check — non-NY and non-legal queries refused immediately.
  3. POST /questions → legal IRAC analysis displayed in chat.
  4. If backend returns offer_complaint=True, silently start intake session.
  5. Subsequent messages also sent to /intake/provide for field extraction.
  6. Generate Complaint button activates when mandatory fields are detected.
  7. If mandatory fields are missing: force-warning panel → Proceed Anyway.
  8. Draft displayed in-page with Download DOCX and Download PDF buttons.

Run:
    streamlit run streamlit_app.py

Environment:
    BACKEND_URL  : FastAPI backend base URL (default: http://localhost:9000)
    API_KEY      : Bearer token for protected endpoints (default: empty)
"""

import streamlit as st
import requests as req_lib

from ny_filter      import check_ny_filter
from backend_client import (
    ask_question,
    start_intake,
    provide_intake,
    force_draft,
    generate_draft,
    download_docx,
    download_pdf,
    get_sessions,
    get_session_history,
)
from styles import get_css

# ──────────────────────────────────────────────
# Page config — must be the first Streamlit call
# ──────────────────────────────────────────────
st.set_page_config(
    page_title = "Nyaay AI — New York Legal Assistant",
    page_icon  = "⚖️",
    layout     = "wide",
)
st.markdown(get_css(), unsafe_allow_html=True)


# ──────────────────────────────────────────────
# Session state
# ──────────────────────────────────────────────
# Flow states:
#   CHAT_ONLY       — no case detected yet, just Q&A
#   INTAKE_ACTIVE   — case detected, collecting fields silently
#   READY_TO_DRAFT  — all mandatory fields filled
#   DRAFTING        — spinner shown while Claude generates
#   DRAFT_COMPLETE  — complaint displayed with download buttons

_DEFAULTS: dict = {
    "messages":          [],       # [{role: "user"|"assistant", content: str}]
    "session_id":        None,     # backend chat session UUID
    "case_id":           None,     # intake case UUID
    "case_type":         None,     # e.g. "personal_injury"
    "required_elements": [],       # list of element dicts from /questions
    "provided_fields":   {},       # fields collected so far
    "missing_fields":    [],       # field IDs still missing (required only)
    "flow_state":        "CHAT_ONLY",
    "draft_text":        None,
    "show_force_warning":False,
    "error_message":     None,
    "pending_input":     None,     # user message waiting to be processed
}


def _init_state() -> None:
    for key, val in _DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _reset_state() -> None:
    """Clear all session state to start a fresh chat."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def _load_session(session_id: str) -> None:
    """Load a previous chat session from the backend into session state."""
    try:
        history = get_session_history(session_id)
        _reset_state()
        _init_state()
        st.session_state.session_id = session_id
        st.session_state.messages   = history
    except Exception:
        pass  # silently fail — user stays on current session


# ──────────────────────────────────────────────
# Intake helpers (silent — no UI side effects)
# ──────────────────────────────────────────────

def _start_intake_silently(session_id: str, case_type: str, initial_text: str) -> None:
    """
    Start the backend intake session after case detection.
    Failures are silently swallowed — chat still works if intake fails.
    """
    try:
        resp = start_intake(session_id, case_type, initial_text)
        st.session_state.case_id       = resp["case_id"]
        st.session_state.provided_fields = dict(resp.get("pre_filled", {}))
        missing_req = resp.get("missing_required", [])
        st.session_state.missing_fields = [f["id"] for f in missing_req]
        st.session_state.flow_state = (
            "READY_TO_DRAFT" if not missing_req else "INTAKE_ACTIVE"
        )
    except Exception:
        # Intake failure is non-fatal — attorney can still chat
        pass


def _update_intake(case_id: str, text: str) -> None:
    """
    Send follow-up text to the intake endpoint and update local field state.
    Silent — does not display anything.
    """
    try:
        resp = provide_intake(case_id, text)
        # Merge newly extracted fields into local state
        newly_filled = resp.get("pre_filled", {})
        st.session_state.provided_fields.update(newly_filled)
        missing_req = resp.get("missing_required", [])
        st.session_state.missing_fields = [f["id"] for f in missing_req]
        if not missing_req:
            st.session_state.flow_state = "READY_TO_DRAFT"
    except Exception:
        pass


# ──────────────────────────────────────────────
# Draft generation helper
# ──────────────────────────────────────────────

def _do_generate() -> None:
    """
    Call generate_draft on the backend. Updates flow_state to DRAFT_COMPLETE
    on success, or back to READY_TO_DRAFT with an error message on failure.
    """
    st.session_state.flow_state = "DRAFTING"
    try:
        result = generate_draft(st.session_state.case_id)
        st.session_state.draft_text  = result["complaint"]
        st.session_state.flow_state  = "DRAFT_COMPLETE"
    except req_lib.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code == 503:
            st.session_state.error_message = (
                "Draft generation timed out. Click Generate to retry."
            )
        else:
            st.session_state.error_message = (
                f"Draft generation failed (HTTP {code}). Please try again."
            )
        st.session_state.flow_state = "READY_TO_DRAFT"
    except req_lib.exceptions.ConnectionError:
        st.session_state.error_message = (
            "Unable to connect to the legal AI service. Please try again."
        )
        st.session_state.flow_state = "READY_TO_DRAFT"
    except Exception as e:
        st.session_state.error_message = f"An unexpected error occurred: {e}"
        st.session_state.flow_state = "READY_TO_DRAFT"


# ──────────────────────────────────────────────
# Message handler
# ──────────────────────────────────────────────

def handle_user_message(user_input: str) -> None:
    """
    Full pipeline for a single attorney message:
      1. Add to chat history
      2. NY filter (frontend)
      3. POST /questions (backend)
      4. Auto-detect case → start intake silently if offer_complaint=True
      5. If intake active → call provide_intake with each message
    """
    # ── Frontend NY filter ────────────────────────────────────────────────────
    # Skip when intake is already active — short follow-up answers ("yes",
    # "i have insurance", "i don't know") look non-legal in isolation but are
    # valid continuations of an established case conversation.
    _case_active = st.session_state.flow_state in ("INTAKE_ACTIVE", "READY_TO_DRAFT", "DRAFTING", "DRAFT_COMPLETE")
    if not _case_active:
        blocked, refusal_msg = check_ny_filter(user_input)
        if blocked:
            st.session_state.messages.append({"role": "assistant", "content": refusal_msg})
            return

    # ── Call backend /questions ───────────────────────────────────────────────
    try:
        resp = ask_question(user_input, st.session_state.session_id)
    except req_lib.exceptions.ConnectionError:
        st.session_state.messages.append({
            "role":    "assistant",
            "content": "Unable to connect to the legal AI service. Please try again.",
        })
        return
    except req_lib.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code == 503:
            msg = "The AI model is still loading. Please wait a moment and try again."
        else:
            msg = f"An error occurred (HTTP {code}). Please try again."
        st.session_state.messages.append({"role": "assistant", "content": msg})
        return
    except Exception:
        st.session_state.messages.append({
            "role":    "assistant",
            "content": "Unable to connect to the legal AI service. Please try again.",
        })
        return

    # ── Update session ID ─────────────────────────────────────────────────────
    st.session_state.session_id = resp.get("session_id", st.session_state.session_id)

    # ── Add assistant answer to chat ──────────────────────────────────────────
    st.session_state.messages.append({"role": "assistant", "content": resp["answer"]})

    # ── Auto case detection ───────────────────────────────────────────────────
    # offer_complaint=True means the backend detected a legal case and classified it.
    # We silently start the intake loop so the Generate button can activate automatically.
    if resp.get("offer_complaint") and st.session_state.flow_state == "CHAT_ONLY":
        st.session_state.case_type         = resp.get("case_type")
        st.session_state.required_elements = resp.get("required_elements", [])
        _start_intake_silently(
            session_id   = st.session_state.session_id,
            case_type    = resp["case_type"],
            initial_text = user_input,
        )
    elif st.session_state.flow_state in ("INTAKE_ACTIVE", "READY_TO_DRAFT") \
            and st.session_state.case_id:
        # Feed every subsequent message into intake for continuous field extraction
        _update_intake(st.session_state.case_id, user_input)


# ──────────────────────────────────────────────
# UI Renderers
# ──────────────────────────────────────────────

def _render_header() -> None:
    st.markdown(
        """
        <div class="nyaay-header">
            <div class="nyaay-logo">⚖️</div>
            <div class="nyaay-header-text">
                <h1>Nyaay AI</h1>
                <p>New York Legal Assistant — Powered by AI</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_chat() -> None:
    """Render all chat messages in order."""
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _render_generate_area() -> None:
    """
    Render the Generate Complaint button and, when active,
    the force-warning panel if mandatory fields are missing.
    """
    flow = st.session_state.flow_state

    # Don't show generate button in terminal states or during drafting
    if flow in ("DRAFTING", "DRAFT_COMPLETE"):
        if flow == "DRAFTING":
            st.info("Generating complaint… this may take up to 60 seconds.")
        return

    st.divider()

    # CHAT_ONLY — button shown but disabled with helper text
    if flow == "CHAT_ONLY":
        col1, col2 = st.columns([3, 1])
        with col2:
            st.button("Generate Complaint", disabled=True, key="gen_disabled")
        with col1:
            st.caption("Describe your New York case to unlock complaint generation.")
        return

    # INTAKE_ACTIVE or READY_TO_DRAFT — button is active
    has_missing = bool(st.session_state.missing_fields)

    # ── Force warning panel ───────────────────────────────────────────────────
    if st.session_state.show_force_warning:
        st.warning(
            "The following required fields are missing and will appear as "
            "**[UNKNOWN]** in the generated complaint:"
        )
        for fid in st.session_state.missing_fields:
            st.markdown(f"&nbsp;&nbsp;• {fid.replace('_', ' ').title()}")
        st.markdown("Do you want to proceed anyway?")

        col1, col2, _ = st.columns([1, 1, 2])
        with col1:
            if st.button("Go Back", key="go_back"):
                st.session_state.show_force_warning = False
                st.rerun()
        with col2:
            if st.button("Proceed Anyway", type="primary", key="proceed_anyway"):
                st.session_state.show_force_warning = False
                try:
                    force_draft(st.session_state.case_id)
                except Exception:
                    pass   # force endpoint failure is non-fatal — draft will still use [UNKNOWN]
                _do_generate()
                st.rerun()
        return

    # ── Normal generate button ────────────────────────────────────────────────
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("Generate Complaint", type="primary", key="gen_active"):
            if has_missing:
                st.session_state.show_force_warning = True
                st.rerun()
            else:
                _do_generate()
                st.rerun()
    with col1:
        if has_missing:
            n = len(st.session_state.missing_fields)
            st.caption(
                f"⚠️ {n} required field{'s' if n > 1 else ''} still missing — "
                "you can proceed anyway and fill in [UNKNOWN] fields before filing."
            )
        else:
            st.caption("✓ All required fields collected — ready to generate complaint.")


def _render_draft() -> None:
    """
    Render the complaint draft and download buttons after generation.
    Only shown when flow_state == DRAFT_COMPLETE.
    """
    if st.session_state.flow_state != "DRAFT_COMPLETE" or not st.session_state.draft_text:
        return

    st.divider()
    st.subheader("📄 Generated Complaint Draft")

    # Read-only draft display — st.text_area(disabled=True) hides text via
    # -webkit-text-fill-color in some Streamlit versions, so use st.code instead.
    st.code(st.session_state.draft_text, language=None, wrap_lines=True)

    # Download buttons + new case
    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        try:
            docx_bytes = download_docx(st.session_state.case_id)
            st.download_button(
                label     = "⬇ Download DOCX",
                data      = docx_bytes,
                file_name = f"complaint_{st.session_state.case_id[:8]}.docx",
                mime      = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key       = "dl_docx",
            )
        except Exception:
            st.error("DOCX download failed. Please try again.")

    with col2:
        try:
            pdf_bytes = download_pdf(st.session_state.case_id)
            st.download_button(
                label     = "⬇ Download PDF",
                data      = pdf_bytes,
                file_name = f"complaint_{st.session_state.case_id[:8]}.pdf",
                mime      = "application/pdf",
                key       = "dl_pdf",
            )
        except Exception:
            st.error("PDF download failed. Please try again.")

    with col3:
        if st.button("Start New Case", key="new_case"):
            # Reset all session state to start fresh
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ──────────────────────────────────────────────
# Left panel — session history (static column)
# ──────────────────────────────────────────────

def _render_left_panel() -> None:
    """Renders the permanent left sidebar using st.markdown — no st.sidebar used."""
    st.markdown(
        """
        <div class="nyaay-panel-brand">
            <span class="nyaay-panel-logo">⚖️</span>
            <span class="nyaay-panel-name">Nyaay AI</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("+ New Chat", type="primary", key="new_chat_btn", use_container_width=True):
        _reset_state()
        st.rerun()

    st.markdown(
        "<p class='nyaay-panel-section'>Recent chats</p>",
        unsafe_allow_html=True,
    )

    sessions = get_sessions()
    real_sessions = [
        s for s in sessions
        if (s.get("title") or "").strip().lower()
        not in ("greeting", "new chat", "untitled", "")
    ]

    if not real_sessions:
        st.markdown(
            "<p class='nyaay-panel-empty'>No previous chats yet.</p>",
            unsafe_allow_html=True,
        )
    else:
        current_session = st.session_state.get("session_id")
        for session in real_sessions[:40]:
            title = session.get("title") or "Untitled"
            title_display = title[:36] + "…" if len(title) > 36 else title
            is_active = session["id"] == current_session
            btn_key = f"sess_{session['id']}"
            if is_active:
                st.markdown(
                    f"<div class='nyaay-session-active'>▸ {title_display}</div>",
                    unsafe_allow_html=True,
                )
            else:
                if st.button(title_display, key=btn_key, use_container_width=True):
                    _load_session(session["id"])
                    st.rerun()


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    _init_state()

    # Two-column static layout: left panel (permanent) | main chat area
    # Using columns instead of st.sidebar so the panel can never be collapsed.
    left_col, main_col = st.columns([1, 4], gap="small")

    with left_col:
        _render_left_panel()

    with main_col:
        _render_header()

        if st.session_state.error_message:
            st.error(st.session_state.error_message)
            st.session_state.error_message = None

        _render_chat()
        _render_generate_area()
        _render_draft()

        # Phase 2 skeleton — rendered inside main_col so it stays in the chat area
        if st.session_state.pending_input:
            pending = st.session_state.pending_input
            st.session_state.pending_input = None
            with st.chat_message("assistant"):
                st.markdown(
                    """
                    <div class="skeleton-wrap">
                        <div class="skeleton-line" style="width:88%"></div>
                        <div class="skeleton-line" style="width:72%"></div>
                        <div class="skeleton-line" style="width:95%"></div>
                        <div class="skeleton-line" style="width:60%"></div>
                        <div class="skeleton-line" style="width:80%"></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            handle_user_message(pending)
            st.rerun()

    # Chat input — always at the bottom of the viewport
    if st.session_state.flow_state not in ("DRAFTING", "DRAFT_COMPLETE"):
        if user_input := st.chat_input("Describe your New York legal case or ask a question…"):
            st.session_state.messages.append({"role": "user", "content": user_input})
            st.session_state.pending_input = user_input
            st.rerun()


if __name__ == "__main__":
    main()
