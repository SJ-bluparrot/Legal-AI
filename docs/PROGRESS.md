# Nyaay AI — Project Progress & Roadmap

> Last updated: 2026-05-08
> Backend: http://localhost:9000 (Claude Haiku + Sonnet via Anthropic API)
> Frontend: ui.html served at http://localhost:9000/ (Streamlit retired)

---

## System Status

| Component | Status | Notes |
|-----------|--------|-------|
| Backend (FastAPI) | ✅ Healthy | Port 9000, `python app.py` |
| Claude Haiku 4.5 | ✅ Active | Conversation + field extraction |
| Claude Sonnet | ✅ Active | Formal complaint generation |
| SQLite session DB | ✅ Working | Sessions persist across visits |
| ANTHROPIC_API_KEY | ✅ Set | Via .env file |
| API_KEY auth | ⚠️ Unset in dev | Intake/draft endpoints unprotected locally |
| NY jurisdiction filter | ✅ Active | Frontend + backend dual enforcement |
| ui.html frontend | ✅ Running | Served by FastAPI at GET / |
| Streamlit frontend | ❌ Retired | Replaced by ui.html |

---

## What Was Built (Complete)

### Backend (`app.py`)
- Multi-turn chat with SQLite session persistence (`sessions`, `messages`, `case_sessions`)
- NY-first jurisdiction filter (3-step: NY allow → non-NY block → foreign block → assume NY)
- Claude Haiku conversational attorney responses (`_haiku_converse`)
- Claude Haiku structured field extraction (`_haiku_extract`) — runs on every `/questions` call
- Case classifier → `offer_complaint` + `case_type` + `required_elements` + `case_id` in every response
- Intake loop (`/intake/start`, `/provide`, `/force`) for silent background field collection
- Complaint drafter using Claude Sonnet with draft lock (no duplicate API charges on retry)
- DOCX + PDF export endpoints
- `/sessions` and `/history/{id}` for session management
- `GET /` serves `ui.html` directly (no separate server needed)

### Supporting Modules
| File | Purpose |
|------|---------|
| `ny_filter.py` | Frontend keyword filter |
| `element_extractor.py` | Required legal elements per case type |
| `entity_extractor.py` | Field extraction from prose |
| `classifier.py` | Case type classification (8 types) |
| `complaint_drafter.py` | Claude Sonnet complaint generation |
| `complaint_router.py` | `/draft/{case_id}` endpoints |
| `intake_router.py` | Stateful intake loop endpoints |
| `docx_router.py` | `/document/{case_id}` DOCX/PDF endpoints |
| `validator.py` | Field-level + SOL validation |
| `utils.py` | `normalize_case_fields()` — replaces missing fields with `[UNKNOWN]` |

### Frontend
- `ui.html` — standalone HTML/CSS/JS, premium legal UI, no build step
- All API calls use relative paths (`/questions`, `/intake/*`, etc.) — same-origin, no CORS issues
- Streamlit (`streamlit_app.py`, `styles.py`, `backend_client.py`) — retained but no longer used

---

## How to Run

```bash
# One command, one port, everything
python app.py
```

- UI: http://localhost:9000
- API docs: http://localhost:9000/docs

No Streamlit. No second terminal.

---

## API Contract (for frontend builds)

### Chat
```
POST /questions
  Body:     { question: string, session_id: string|null }
  Response: { answer, session_id, offer_complaint, case_id, case_type, required_elements }
  Note:     case_id is included when offer_complaint=true so frontend can draft without intake
```

### Sessions
```
GET    /sessions
GET    /history/{session_id}
POST   /history/{session_id}/clear
DELETE /sessions/{session_id}
```

### Intake (silent, starts when offer_complaint = true)
```
POST  /intake/start                  { session_id, case_type, initial_text }
  Response includes: provided_fields (full cumulative), pre_filled (this turn only),
                     missing_required, sections_display
POST  /intake/{case_id}/provide      { text }
  Response includes: provided_fields (full cumulative), pre_filled (this turn only)
PATCH /intake/{case_id}/force
GET   /case/{case_id}/progress       → { percent, missing_fields, provided_fields }
```

### Draft + Export
```
POST /draft/{case_id}               → { complaint, word_count, unknown_count }
POST /document/{case_id}            → DOCX download
POST /document/{case_id}/pdf        → PDF download
```

### Frontend state machine
```
CHAT_ONLY → (offer_complaint=true) → INTAKE_ACTIVE → (MVP fields met) →
READY_TO_DRAFT → (Generate clicked) → DRAFTING → DRAFT_COMPLETE
```
Skip NY filter when state is INTAKE_ACTIVE or later.

---

## Supported Case Types
`personal_injury`, `employment_dispute`, `contract_dispute`, `property_damage`,
`eminent_domain`, `criminal_defense`, `family_law`

`"other"` cannot produce a complaint.

---

## Fixes Applied — Session 2026-05-08

### Bug: Case Elements panel always showing 0% / "Awaiting case description..."
**Root cause**: `_build_intake_response` in `intake_router.py` only returned `pre_filled` (fields
extracted in the current turn). The full cumulative `provided_fields` (including everything Haiku
captured during chat via `/questions`) was stored in the DB but never sent back to the client.
The frontend built its field state from `pre_filled` alone, missing all Haiku-extracted fields.

**Fix** (`intake_router.py`):
- Added `provided_fields` (full cumulative dict) to every `/intake/start` and `/intake/provide` response

**Fix** (`ui.html`):
- `apiIntakeStart`: `S.provided = d.provided_fields || d.pre_filled || {}`
- `apiProvide`: `S.provided = d.provided_fields || { ...S.provided, ...(d.pre_filled||{}) }`
- `updatePanel`: `const provided = d.provided_fields || d.pre_filled || S.provided || {}`
- `populateReadyScreen`: same fallback chain

**Fix** (`streamlit_app.py`):
- `_start_intake_silently`: uses `provided_fields` first, falls back to `pre_filled`
- `_update_intake`: replaces full state from `provided_fields` rather than merging only `pre_filled`

---

### Bug: "Generate Complaint" button never appeared even when AI said "Ready to generate"
**Root cause**: The button only appeared after `/intake/start` succeeded AND returned zero missing
fields. If intake start failed silently (`if (!r.ok) return`) — which happened when the backend
returned an error — the button was permanently hidden. The frontend had no fallback.

**Fix** (`app.py`):
- Added `case_id: str | None` to `QuestionResponse` model
- `_get_case_state_for_session` now returns `case_id` in its dict
- `/questions` includes `case_id` in the response when `offer_complaint=True`

**Fix** (`ui.html`):
- When `offer_complaint=True` from `/questions`, the Generate button is shown immediately
  using `d.case_id` — no longer waits for `/intake/start` to succeed
- Intake start still runs in the background to populate the fields panel
- `handleGenerate()` now attempts an on-demand intake start if `S.caseId` is still null

---

### Bug: Case Summary on "Ready to Draft" screen showed raw markdown (`#`, `*`, `**`)
**Root cause**: `populateReadyScreen` used `element.textContent = last.content.slice(0,350)`
which dumps raw AI response text including markdown symbols.

**Fix** (`ui.html`):
- Changed `#cr-summary` from `<p>` to `<div>` (block children need a block container)
- Replaced `textContent` assignment with `marked.parse()` + `innerHTML`
- Added `md-prose` class so heading/list/bold CSS styles apply

---

### Bug: Generated complaint was cut off mid-paragraph
**Root cause**: `CLAUDE_MAX_TOKENS = 1800` in `complaint_drafter.py`. A full NY CPLR Verified
Complaint with 20–30 numbered paragraphs, WHEREFORE clause, and signature block requires
2000–3500 tokens. The draft was being truncated wherever the limit was hit.

**Fix** (`complaint_drafter.py`):
- Raised `CLAUDE_MAX_TOKENS` from `1800` → `4096`

---

### Bug: Markdown symbols visible in draft screen conversation history
**Root cause**: `renderDraft()` used `esc(msg.content.slice(0,200))` for all messages. `esc()`
HTML-escapes everything, so AI responses with `**bold**`, `##` headings etc. showed as raw symbols.

**Fix** (`ui.html`):
- AI messages in draft history now use `marked.parse(msg.content)` with `md-prose` class
- User messages still use `esc()` (user input should never be parsed as markdown)
- Removed the 200-character truncation on AI messages

---

## Active Issues / Next Steps

### High value
- [ ] **SOL per legal theory** — Add correct NY filing deadlines to IRAC response (personal injury: 3yr CPLR §214, employment/Title VII: 300 days EEOC, medical malpractice: 2.5yr CPLR §214-a).
- [ ] **Case type acknowledgement in response** — Classifier fires but response text ignores it. IRAC answer should reference detected case type.

### Frontend (ui.html)
- [ ] Streaming responses (SSE endpoint + frontend `EventSource`)
- [ ] Copy-to-clipboard per message
- [ ] Collapsible IRAC sections (Facts / Law / Analysis / Remedies)

### Performance
- [ ] Reduce `MAX_HISTORY_MESSAGES` from 5 → 2–3 pairs (prompts hit 1600+ tokens by turn 3)

---

## Critical Dependency Pins (DO NOT UPGRADE)

| Package | Pin | Reason |
|---------|-----|--------|
| `accelerate` | ==0.31.0 | 1.x breaks bitsandbytes 8-bit |
| `bitsandbytes` | ==0.43.3 | 0.44+ breaks torch 2.4.0 |
| `transformers` | ==4.42.4 | 5.x breaks bitsandbytes 8-bit loading |
