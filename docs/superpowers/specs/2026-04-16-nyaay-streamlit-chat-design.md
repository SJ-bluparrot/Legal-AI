# Nyaay AI — Streamlit Chat Interface Design Spec
**Date:** 2026-04-16  
**Status:** Approved for implementation  
**Author:** Brainstorming session with client

---

## Overview

Build a Streamlit web app that gives New York attorneys a ChatGPT/Claude-style chat interface backed by the SaulLM-AI FastAPI backend. Attorneys describe cases in plain English, the system extracts fields automatically, and when enough information is collected a Generate button unlocks to produce a formal legal complaint exportable as DOCX or PDF.

---

## Goals

- Chat-first UX — attorneys type naturally, no forms
- New York only — default assume NY, refuse explicit non-NY and non-legal queries
- Automatic case detection — no "Start Case" button, the system detects a case from conversation
- Generate button unlocks when mandatory fields are filled
- Draft shown in-page with Download DOCX and Download PDF buttons
- Sessionless MVP — no login, fresh state per browser visit
- Nyaay AI branding — dark navy/blue gradient, cyan accents

---

## Architecture

### File Structure

```
SaulLM-AI/
  streamlit_app.py       # UI only — layout, chat rendering, button logic
  backend_client.py      # All FastAPI API calls — single place for all HTTP requests
  ny_filter.py           # New York filter logic — used by frontend before API call
  styles.py              # Nyaay AI CSS theme injected via st.markdown

  app.py                 # MODIFIED — NY-first jurisdiction filter replaces US/UK filter
```

### Component Responsibilities

| File | Does | Does NOT do |
|------|------|-------------|
| `streamlit_app.py` | Renders UI, manages session_state, drives flow | Makes HTTP calls, filter logic |
| `backend_client.py` | Wraps all API calls, handles errors | UI, filtering |
| `ny_filter.py` | Fast NY keyword check | API calls, UI |
| `styles.py` | CSS string for Nyaay theme | Logic of any kind |
| `app.py` (modified) | NY-first jurisdiction filter | Frontend concerns |

---

## New York Filter

### Logic (3-step allowlist, same pattern as existing US/UK filter)

1. **NY signal found** → ALLOW (explicitly New York)
2. **No location signal** → ALLOW (assume New York — default)
3. **Explicit non-NY signal found** → BLOCK

### NY Signals (allowlist)
- `"new york"`, `"ny"`, `"nyc"`, `"manhattan"`, `"brooklyn"`, `"queens"`, `"bronx"`, `"staten island"`, `"long island"`, `"buffalo"`, `"albany"`, `"new york city"`, `"new york state"`
- NY courts: `"new york supreme court"`, `"new york court of appeals"`, `"southern district of new york"`, `"eastern district of new york"`, `"sdny"`, `"edny"`
- NY law: `"new york law"`, `"ny law"`, `"cplr"` (NY Civil Practice Law and Rules)

### Non-NY US State Signals (blocklist — other 49 states + their major cities)
- All US states except New York
- Foreign country signals (already in existing backend)

### Response messages
- **Non-NY US**: "This assistant handles **New York legal matters only**. Your question appears to reference [state]. Please consult a qualified attorney in that jurisdiction."
- **Foreign jurisdiction**: "This assistant handles **New York legal matters only**. Your question appears to reference a foreign jurisdiction..."
- **Non-legal**: "This assistant handles **New York legal cases only**. Your question does not appear to describe a legal situation..."

### Two-layer enforcement
- `ny_filter.py` — frontend fast-reject before API call (instant feedback)
- `app.py` — backend enforcement (source of truth, modified from US/UK to NY-first)

---

## Chat Flow & State Machine

### States (managed in `st.session_state`)

```
CHAT_ONLY
    ↓  (backend returns offer_complaint=True)
INTAKE_ACTIVE         ← silently starts /intake/start in background
    ↓  (mandatory fields all filled)
READY_TO_DRAFT        ← Generate button becomes active
    ↓  (user clicks Generate or Proceed Anyway)
DRAFTING              ← spinner shown
    ↓  (draft returned)
DRAFT_COMPLETE        ← draft displayed + download buttons shown
```

### Message Routing (after INTAKE_ACTIVE state)

Every attorney message goes to TWO endpoints in parallel:
1. `POST /questions` — for the IRAC legal answer displayed in chat
2. `POST /intake/{case_id}/provide` — for field extraction (silent, updates state)

### session_state keys

```python
st.session_state.messages         # list of {role, content} for chat display
st.session_state.session_id       # backend chat session UUID
st.session_state.case_id          # intake case UUID (None until case detected)
st.session_state.case_type        # detected case type string
st.session_state.required_elements # list of required field dicts
st.session_state.provided_fields  # dict of filled fields
st.session_state.missing_fields   # list of still-missing field IDs
st.session_state.flow_state       # one of: CHAT_ONLY, INTAKE_ACTIVE, READY_TO_DRAFT, DRAFTING, DRAFT_COMPLETE
st.session_state.draft_text       # complaint text after generation
```

---

## Generate Button Behavior

### Normal path (all mandatory fields filled)
- Button label: "Generate Complaint"
- Button state: Active (cyan, clickable)
- On click: calls `POST /draft/{case_id}` → shows spinner → displays draft

### Force path (mandatory fields still missing)
- Button label: "Generate Complaint"  
- Button state: Active but shows warning indicator
- On click: Shows warning panel listing all missing mandatory fields with a message:
  > "The following required fields are missing and will appear as [UNKNOWN] in the complaint: [field list]. Do you want to proceed anyway?"
- Two buttons: **"Go Back"** (dismisses warning) and **"Proceed Anyway"** (calls `PATCH /intake/{case_id}/force` then `POST /draft/{case_id}`)

### When Generate button appears
- Button is always visible in the UI once `flow_state == INTAKE_ACTIVE`
- Disabled (greyed) while in CHAT_ONLY state
- Enabled once mandatory fields are detected

---

## Draft Display & Downloads

After generation, below the chat:
1. **Complaint Draft** section — full complaint text in a scrollable text area (read-only, styled to match theme)
2. **Download DOCX** button — calls `POST /document/{case_id}` with `format=docx`
3. **Download PDF** button — calls `POST /document/{case_id}` with `format=pdf`
4. **Start New Case** button — resets all session_state to start fresh

---

## Nyaay AI Theme

### Color Palette
| Token | Value | Usage |
|-------|-------|-------|
| `--bg-dark` | `#0A1628` | Page background, chat bg |
| `--bg-card` | `#0F1E3D` | Message bubbles, cards |
| `--blue-mid` | `#1E3FCC` | Gradient midpoint |
| `--cyan` | `#38BDF8` | Accents, active buttons, highlights |
| `--text-primary` | `#FFFFFF` | Main text |
| `--text-secondary` | `#94A3B8` | Muted text, timestamps |
| `--border` | `rgba(56,189,248,0.2)` | Card borders |

### Key CSS overrides (injected via `styles.py`)
- Hide Streamlit default header/footer/menu
- Dark gradient background full-page
- Chat bubbles: user (cyan tinted), assistant (dark navy card)
- Generate button: cyan gradient, disabled state grey
- Input box: dark with cyan focus border

---

## Backend Modification (`app.py`)

### Change: Replace jurisdiction filter

**Remove**: `US_LOCATIONS`, `UK_LOCATIONS`, `FOREIGN_LOCATIONS` sets and `is_unsupported_jurisdiction()` function  
**Add**: `NY_LOCATIONS` allowlist, `NON_NY_US_LOCATIONS` blocklist, `is_unsupported_jurisdiction()` updated with NY-first 3-step logic

**Response message update**: Change from "US and UK legal matters only" → "New York legal matters only"

### No other backend changes required
All other endpoints (`/intake/*`, `/draft/*`, `/document/*`, `/validate/*`) work as-is.

---

## Error Handling

| Scenario | Frontend behavior |
|----------|-------------------|
| Backend unreachable | Show inline error: "Unable to connect to the legal AI service. Please try again." |
| Model still loading (503) | Show: "The AI model is still loading, please wait a moment and try again." |
| Non-NY query | Show refusal message inline as assistant message (no API call made) |
| Non-legal query | Show refusal message inline as assistant message (no API call made) |
| Draft generation timeout | Show: "Draft generation timed out. Click Generate to retry." |
| Download failure | Show inline error below the download button |

---

## Out of Scope (MVP)

- User authentication / accounts
- Case history across sessions
- Multi-jurisdiction support
- Real-time streaming responses
- Mobile responsiveness optimization
- Admin dashboard

---

## Success Criteria

1. Attorney can type a NY legal case description and receive an IRAC analysis
2. Non-NY and non-legal queries are refused with a clear message
3. Generate button activates automatically when mandatory fields are detected
4. Force path works — attorney can proceed with missing fields after warning
5. Draft displays in-page after generation
6. DOCX and PDF downloads work
7. Nyaay AI dark theme is consistent throughout
8. Page does not crash on backend errors
