# Nyaay AI — Project Progress & Roadmap

> Last updated: 2026-05-05
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
- Case classifier → `offer_complaint` + `case_type` + `required_elements` in every response
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
  Response: { answer, session_id, offer_complaint, case_type, required_elements }
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
POST  /intake/start                  { case_id, chat_session_id, case_type }
POST  /intake/{case_id}/provide      { text }
PATCH /intake/{case_id}/force
GET   /case/{case_id}/progress       → { percent, missing_fields, provided_fields }
```

### Draft + Export
```
POST /draft/{case_id}               → { draft: string }
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

## Active Issues / Next Steps

### High value
- [ ] **Entity extraction in `/questions`** — Pre-seed intake with fields from the first message. Currently entity extraction only fires at `/intake/start`, discarding structured data from the question itself.
- [ ] **SOL per legal theory** — Add correct NY filing deadlines to IRAC response (personal injury: 3yr CPLR §214, employment/Title VII: 300 days EEOC, medical malpractice: 2.5yr CPLR §214-a).
- [ ] **Case type acknowledgement in response** — Classifier fires but response text ignores it. IRAC answer should reference detected case type.

### Frontend (ui.html redesign)
- [ ] New UI design replacing current ui.html
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
