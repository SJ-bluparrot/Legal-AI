# Nyaay AI — API Integration Guide

**For full-stack teams building a frontend against the Nyaay FastAPI backend.**

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Architecture Overview](#2-architecture-overview)
3. [Authentication](#3-authentication)
4. [Rate Limiting & CORS](#4-rate-limiting--cors)
5. [Endpoint Reference](#5-endpoint-reference)
   - [Health](#51-health)
   - [Chat](#52-chat--questions)
   - [Sessions & History](#53-sessions--history)
   - [Intake](#54-intake)
   - [Draft Complaint](#55-draft-complaint)
   - [Documents](#56-documents-docx--pdf)
6. [End-to-End Flow](#6-end-to-end-flow)
7. [Frontend State Machine](#7-frontend-state-machine)
8. [Case Types & Field Schemas](#8-case-types--field-schemas)
9. [Error Reference](#9-error-reference)
10. [Environment Variables](#10-environment-variables)

---

## 1. Quick Start

```bash
# Start the backend
uvicorn app:app --host "::" --port 8000

# Verify it's alive
curl http://localhost:8000/health
# → {"status":"healthy","api":"claude","timestamp":"2026-..."}

# Open the built-in chat UI
open http://localhost:8000/ui.html
```

**Base URL:** `http://localhost:8000` (dev) — set `ALLOWED_ORIGINS` env var for production CORS.

**Swagger / OpenAPI docs:** `http://localhost:8000/docs`

---

## 2. Architecture Overview

The backend is a **FastAPI** application (`app.py`) with three sub-routers:

```
POST /questions        ← public chat endpoint (Claude Haiku × 2)
GET  /sessions         ← session list
GET  /history/:id      ← message history
─────────────────────────────────────────────────────── bearer-auth below ───
POST /intake/start     ← begin structured intake
POST /intake/:id/provide  ← add more facts
GET  /intake/:id          ← read intake state
GET  /intake/validate/:id ← validate fields
PATCH /intake/:id/force   ← bypass missing fields
GET  /case/:id/progress   ← completion %
─────────────────────────────────────────────────────────────────────────────
POST /draft/:id        ← generate complaint (Claude Sonnet)
GET  /draft/:id        ← retrieve cached draft
─────────────────────────────────────────────────────────────────────────────
POST /document/:id         ← download DOCX
POST /document/:id/pdf     ← download PDF
GET  /document/:id/status  ← check draft readiness
```

**Two AI models handle distinct roles:**

| Model | Role | Triggered by |
|---|---|---|
| Claude Haiku (`claude-haiku-4-5-20251001`) | Conversational attorney responses + structured JSON field extraction | Every `POST /questions` call (two calls per request) |
| Claude Sonnet (`claude-sonnet-4-6`) | Formal complaint document generation | `POST /draft/:case_id` |

**Storage:** SQLite (`chat_history.db`) with WAL mode. Three tables: `sessions`, `messages`, `case_sessions`.

---

## 3. Authentication

**Public endpoints** (no auth required):
- `GET /health`
- `GET /ui`, `GET /ui.html`
- `POST /questions`
- `GET /sessions`
- `GET /history/:session_id`
- `POST /history/:session_id/clear`
- `DELETE /sessions/:session_id`

**Protected endpoints** (Bearer token required in production):
- All `/intake/*` routes
- All `/draft/*` routes
- All `/document/*` routes
- `GET /case/:case_id/progress`

**Dev mode:** If the `API_KEY` env var is **empty or unset**, auth is skipped entirely — all endpoints are open. This is the default local dev behavior.

**Production:** Set `API_KEY=your-secret` in `.env`. Every request to a protected route must include:

```http
Authorization: Bearer your-secret
```

**401** is returned if the header is missing. **403** if the token is wrong.

---

## 4. Rate Limiting & CORS

**Rate limit:** `POST /questions` is limited to **30 requests/minute per IP** via `slowapi`. Hitting the limit returns `429 Too Many Requests`.

**CORS:** Controlled by the `ALLOWED_ORIGINS` env var (comma-separated list). Default: `http://localhost:3000,http://localhost:5173`. Set this to your production frontend origin before deploying.

```bash
export ALLOWED_ORIGINS="https://yourapp.com,https://www.yourapp.com"
```

Allowed methods: `GET, POST, PATCH, DELETE`. Allowed headers: `Authorization, Content-Type`.

---

## 5. Endpoint Reference

---

### 5.1 Health

#### `GET /health`

Returns server and AI backend status. No auth required. Use for uptime checks.

**Response 200:**
```json
{
  "status": "healthy",
  "api": "claude",
  "timestamp": "2026-05-04T10:30:00.000000"
}
```

---

### 5.2 Chat — `/questions`

#### `POST /questions`

The **main conversational endpoint**. Sends a user message to the AI attorney assistant. Returns a natural-language response plus structured metadata about the case.

Under the hood this makes **two Claude Haiku calls**:
1. Generate the conversational attorney response.
2. Extract structured JSON fields from the conversation history.

**Request body:**
```json
{
  "question": "My client Jane Doe was hit by John Smith who ran a red light...",
  "session_id": "optional-uuid-to-continue-an-existing-session"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `question` | string | yes | Max 2,000 chars. Blank strings return 422. |
| `session_id` | string (UUID) | no | Omit on first message; pass on follow-ups. |

**Response 200:**
```json
{
  "answer": "This is a strong personal injury matter under NY law...",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "offer_complaint": true,
  "case_type": "personal_injury",
  "required_elements": [
    {
      "id": "plaintiff_name",
      "label": "Plaintiff Name",
      "description": "Full legal name of the plaintiff",
      "required": true,
      "section": "Parties"
    }
  ],
  "sections": {
    "Parties": { "fields": ["plaintiff_name", "defendant_name"] },
    "Incident Details": { "fields": ["incident_date", "incident_location"] }
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `answer` | string | Display this in the chat UI |
| `session_id` | string | **Save this.** Pass it in all subsequent requests. |
| `offer_complaint` | boolean | `true` = MVP fields are collected. Prompt the user to generate a complaint. |
| `case_type` | string | One of the 7 supported types, `"other"`, or `"unsupported"` (jurisdiction block) |
| `required_elements` | array | All fields for this case type. Use to build the intake panel. |
| `sections` | object | Grouping of fields for a sectioned UI layout |

**Key frontend logic:**
- Store `session_id` in local storage after the first response.
- When `offer_complaint === true`, call `POST /intake/start` and prompt the user to confirm generation.
- `case_type === "unsupported"` means the query was for a non-NY jurisdiction — show a soft rejection message (the `answer` field contains it).

**Errors:**
- `422` — empty/blank question, or missing `question` field
- `429` — rate limit exceeded (30/min per IP)

---

### 5.3 Sessions & History

#### `GET /sessions`

Returns all chat sessions, newest first. Use to populate a session list in the sidebar.

**Response 200:** Array of session objects.
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "My client Jane Doe was hit by...",
    "created_at": "2026-05-04T10:00:00"
  }
]
```

---

#### `GET /history/{session_id}`

Returns the full message history for a session, oldest first. Use to restore a conversation on page reload.

**Response 200:**
```json
[
  { "role": "user",      "content": "My client...",    "timestamp": "2026-05-04T10:00:00" },
  { "role": "assistant", "content": "This is a...",    "timestamp": "2026-05-04T10:00:01" }
]
```

**Errors:** `404` if session not found.

---

#### `POST /history/{session_id}/clear`

Deletes all messages for a session but keeps the session record. Useful for a "clear chat" button.

**Response 200:**
```json
{ "message": "Chat history cleared.", "session_id": "..." }
```

**Errors:** `404` if session not found.

---

#### `DELETE /sessions/{session_id}`

Deletes the session and all its messages permanently.

**Response 200:**
```json
{ "message": "Session deleted.", "session_id": "..." }
```

---

### 5.4 Intake

> All intake endpoints require **`Authorization: Bearer <API_KEY>`** in production.

Intake is the **silent background field collection** system. After `offer_complaint` is `true` from `/questions`, you start an intake session and pipe subsequent user messages through it. The backend extracts structured fields from natural prose automatically — you never show a form.

---

#### `POST /intake/start`

Creates a new intake case session. Call this immediately after `offer_complaint` becomes `true`.

**Request body:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "case_type": "personal_injury",
  "initial_text": "Jane Doe was hit by John Smith who ran a red light..."
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | string | yes | The `session_id` from `/questions` |
| `case_type` | string | yes | Must be one of the 7 supported types (see §8) |
| `initial_text` | string | yes | The last user message that triggered `offer_complaint`. Max 5,000 chars. |

**Response 200** (same shape for all intake endpoints):
```json
{
  "case_id": "abc12345-...",
  "case_type": "personal_injury",
  "is_complete": false,

  "pre_filled": {
    "plaintiff_name": "Jane Doe",
    "defendant_name": "John Smith",
    "incident_date": "March 10 2025",
    "incident_location": "Broadway and 34th Street, Manhattan"
  },

  "missing_required": [
    {
      "id": "injury_description",
      "label": "Injury Description",
      "description": "Physical injuries sustained by the plaintiff",
      "section": "Incident Details"
    }
  ],

  "missing_questions": [
    "What injuries did the plaintiff suffer?"
  ],

  "missing_optional": [
    { "id": "witness_names", "label": "Witness Names", "section": "Evidence" }
  ],

  "sections_display": {
    "Parties": {
      "fields": [
        { "id": "plaintiff_name", "label": "Plaintiff Name", "required": true,
          "value": "Jane Doe", "filled": true },
        { "id": "defendant_name", "label": "Defendant Name", "required": true,
          "value": "John Smith", "filled": true }
      ]
    },
    "Incident Details": {
      "fields": [
        { "id": "incident_date", "label": "Date of Incident", "required": true,
          "value": "March 10 2025", "filled": true },
        { "id": "injury_description", "label": "Injury Description", "required": true,
          "value": null, "filled": false }
      ]
    }
  },

  "validation": {
    "is_valid": false,
    "can_draft": false,
    "issues": [
      { "field": "injury_description", "severity": "error", "message": "Required field missing." }
    ],
    "sol_warning": null,
    "validation_summary": "1 required field missing.",
    "draft_readiness_score": 72
  }
}
```

**Key fields to use:**

| Field | Frontend use |
|---|---|
| `case_id` | **Save this.** Used in all subsequent intake/draft/document calls. |
| `is_complete` | `true` = all required fields collected. Prompt user to generate complaint. |
| `pre_filled` | Fields auto-extracted from text. Show in the case panel. |
| `missing_required` | Required fields still empty. Ask for these in chat. |
| `missing_questions` | Human-readable questions for each missing required field. Use these verbatim. |
| `sections_display` | Full field list grouped by section. Use to render an intake panel. |
| `validation.can_draft` | `true` = safe to call `POST /draft/:case_id`. |
| `validation.draft_readiness_score` | 0–100. Use for a progress bar. |

**Errors:**
- `400` — invalid `case_type`
- `413` — `initial_text` > 5,000 chars

---

#### `POST /intake/{case_id}/provide`

Send a follow-up message from the user during intake. The backend extracts any new fields and merges them.

**Auth required.**

**Request body:**
```json
{ "text": "She suffered a broken leg and $15,000 in medical bills." }
```

**Response 200:** Same shape as `/intake/start`. `pre_filled` in the response contains **only** the fields newly extracted in this turn (not cumulative). `sections_display` is always cumulative.

**Errors:**
- `404` — case not found
- `422` — blank text
- `413` — text > 5,000 chars

---

#### `GET /intake/{case_id}`

Returns the full current state of an intake session. Use to restore intake state on page reload.

**Auth required.**

**Response 200:** Same shape as `/intake/start`, plus:
```json
{
  "chat_session_id": "550e8400-...",
  "force_draft": false,
  "provided_fields": { "plaintiff_name": "Jane Doe", ... },
  "created_at": "2026-05-04T10:00:00",
  "updated_at": "2026-05-04T10:05:00"
}
```

---

#### `GET /intake/validate/{case_id}`

Returns the latest validation result for a case session. Lighter than `GET /intake/:id` — use this to gate the Generate button.

**Auth required.**

**Response 200:**
```json
{
  "case_id": "abc12345-...",
  "case_type": "personal_injury",
  "is_valid": true,
  "can_draft": true,
  "missing_required": [],
  "missing_optional": ["witness_names"],
  "issues": [],
  "sol_warning": null,
  "validation_summary": "Ready to draft.",
  "draft_readiness_score": 100
}
```

---

#### `PATCH /intake/{case_id}/force`

Marks the intake as `force_draft=true`, allowing complaint generation even with missing required fields. Missing fields will appear as `[UNKNOWN]` in the final document.

**Auth required.** No request body.

**Response 200:**
```json
{
  "case_id": "abc12345-...",
  "force_draft": true,
  "message": "Proceeding to draft with missing fields marked as [UNKNOWN].",
  "missing_required": [
    { "id": "injury_description", "label": "Injury Description", "section": "Incident Details" }
  ],
  "missing_count": 1
}
```

Show `missing_required` to the user so they know what will be `[UNKNOWN]` in the complaint.

---

#### `GET /case/{case_id}/progress`

Returns a simple completion summary. Use for a progress bar or status indicator.

**Auth required.**

**Response 200:**
```json
{
  "case_id": "abc12345-...",
  "fields_completed": 8,
  "fields_total": 12,
  "progress": "67%",
  "required_completed": 6,
  "required_total": 8,
  "optional_completed": 2,
  "optional_total": 4,
  "missing_questions": [
    "What injuries did the plaintiff suffer?",
    "What negligent act caused the injury?"
  ]
}
```

---

### 5.5 Draft Complaint

> Requires **`Authorization: Bearer <API_KEY>`** in production.

---

#### `POST /draft/{case_id}`

Generates the formal legal complaint using **Claude Sonnet**. This is the expensive call — it costs API credits and takes 5–15 seconds.

**Draft lock:** If `draft_generated` is already `true` for the case, the cached draft is returned immediately without calling Claude. The `from_cache` field in the response will be `true`.

**No request body required.** (Send an empty `{}` or no body.)

**Response 200:**
```json
{
  "case_id": "abc12345-...",
  "case_type": "personal_injury",
  "complaint": "SUPREME COURT OF THE STATE OF NEW YORK\nCOUNTY OF NEW YORK\n\nJANE DOE, Plaintiff,\n\n-against-\n\nJOHN SMITH, Defendant.\n\n...",
  "from_cache": false,
  "word_count": 912,
  "unknown_count": 0
}
```

| Field | Notes |
|---|---|
| `complaint` | Full complaint text. Display in a read-only text area or pre-formatted block. |
| `from_cache` | `true` = no API call was made; served from DB. |
| `word_count` | Approximate word count of the generated document. |
| `unknown_count` | Number of `[UNKNOWN]` placeholders remaining. Alert the user if > 0. |

**Errors:**
- `404` — case not found
- `422` — required fields missing and `force_draft` is `false`. Body contains `missing_count` and `fields[]` with questions.
- `503` — Claude API unavailable

---

#### `GET /draft/{case_id}`

Returns the cached complaint draft without calling Claude. Returns `404` if no draft has been generated yet.

**Response 200:** Same shape as `POST /draft/:case_id` with `from_cache: true`.

---

### 5.6 Documents (DOCX + PDF)

> Requires **`Authorization: Bearer <API_KEY>`** in production.

Both endpoints generate the file on demand from the stored `draft_text`. They do **not** persist the file to disk.

---

#### `POST /document/{case_id}`

Download the complaint as a `.docx` Word document.

**Request body (optional):**
```json
{ "attorney_name": "Jane Smith, Esq." }
```

If `attorney_name` is omitted, `[ATTORNEY NAME]` appears in the signature block.

**Response 200:**
- `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `Content-Disposition: attachment; filename="complaint_personal-injury_abc12345.docx"`
- Binary `.docx` body

**Frontend:** Use `URL.createObjectURL(blob)` and an `<a download>` element to trigger the browser download:
```js
const r = await fetch(`/document/${caseId}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
  body: JSON.stringify({ attorney_name: 'Jane Smith, Esq.' })
});
const blob = await r.blob();
const a = document.createElement('a');
a.href = URL.createObjectURL(blob);
a.download = `complaint_${caseId.slice(0, 8)}.docx`;
a.click();
```

**Errors:**
- `404` — case not found
- `422` — draft not yet generated (call `POST /draft/:case_id` first)
- `500` — DOCX generation failed

---

#### `POST /document/{case_id}/pdf`

Download the complaint as a `.pdf`. Identical to the DOCX endpoint but returns:
- `Content-Type: application/pdf`
- `Content-Disposition: attachment; filename="complaint_personal-injury_abc12345.pdf"`

**Request body (optional):** Same as DOCX — `{ "attorney_name": "..." }`.

---

#### `GET /document/{case_id}/status`

Check whether a draft exists and is ready to export. Use to enable/disable the download buttons without generating the file.

**Response 200:**
```json
{
  "case_id": "abc12345-...",
  "draft_exists": true,
  "case_type": "personal_injury",
  "word_count": 912,
  "unknown_count": 0,
  "ready_to_export": true
}
```

---

## 6. End-to-End Flow

This is the canonical flow from first message to downloaded complaint. Every step maps to one or more API calls.

```
User types message
       │
       ▼
POST /questions
  ├── answer          → display in chat
  ├── session_id      → store in localStorage
  ├── offer_complaint == false
  │     └── continue chatting (loop back)
  └── offer_complaint == true
        │
        ▼
POST /intake/start
  ├── case_id         → store in localStorage
  ├── pre_filled      → show in case panel
  ├── is_complete == true
  │     └── ask user "Want to generate the complaint?"
  └── is_complete == false
        │   missing_questions → ask in chat
        │
        ▼  (user answers)
POST /intake/{case_id}/provide        (loop until is_complete == true)
  └── is_complete == true
        │
        ▼
Ask user: "Generate complaint now?"
        │ user says yes
        ▼
POST /draft/{case_id}
  ├── complaint       → show in panel
  ├── unknown_count > 0 → warn user about [UNKNOWN] fields
  └── done
        │
        ├─ POST /document/{case_id}       → download DOCX
        └─ POST /document/{case_id}/pdf   → download PDF
```

**Parallel channels:** While the user chats via `POST /questions`, pipe every user message to **both** `/questions` (for the conversational response) and `POST /intake/{case_id}/provide` (for silent field extraction) once intake is active.

---

## 7. Frontend State Machine

Implement a `flow` state variable with these values:

```
CHAT_ONLY
  ├── receive offer_complaint == true → POST /intake/start → INTAKE_ACTIVE
  └── receive offer_complaint == false → stay CHAT_ONLY

INTAKE_ACTIVE
  ├── is_complete == true → READY_TO_DRAFT
  └── provide more info → stay INTAKE_ACTIVE

READY_TO_DRAFT
  ├── user confirms "yes, generate" → POST /draft/:id → DRAFTING
  └── user adds more info → POST /intake/:id/provide → back to check is_complete

DRAFTING
  └── draft response received → DRAFT_COMPLETE

DRAFT_COMPLETE
  ├── POST /document/:id     → DOCX download
  └── POST /document/:id/pdf → PDF download
```

**NY filter bypass:** When `flow` is `INTAKE_ACTIVE`, `READY_TO_DRAFT`, `DRAFTING`, or `DRAFT_COMPLETE`, skip any client-side NY jurisdiction check — short answers like "yes", "I don't know", "December 5th" must not be filtered.

**Confirm before generating:** When `is_complete` becomes `true`, display a conversational message asking the user to confirm — do not call `POST /draft/:id` automatically.

**Force draft path:** Show a "Draft with missing fields →" button when `missing_required.length > 0`. Clicking it calls `PATCH /intake/:id/force` then enables the generate flow.

---

## 8. Case Types & Field Schemas

These are the 7 supported case types. `"other"` is intentionally unsupported — it has no field schema and cannot produce a complaint.

| `case_type` | Display name | MVP fields required to offer draft |
|---|---|---|
| `personal_injury` | Personal Injury | `plaintiff_name`, `incident_date`, `incident_location`, `injury_description` |
| `employment_dispute` | Employment Dispute | `plaintiff_name`, `defendant_name`, `dispute_type`, `dispute_description` |
| `contract_dispute` | Contract Dispute | `plaintiff_name`, `defendant_name`, `contract_description`, `breach_description` |
| `property_damage` | Property Damage | `plaintiff_name`, `incident_date`, `damage_description` |
| `eminent_domain` | Eminent Domain | `plaintiff_name`, `defendant_name`, `property_address` |
| `criminal_defense` | Criminal Defense | `defendant_name`, `charges`, `court_name` |
| `family_law` | Family Law | `plaintiff_name`, `defendant_name` |

**All fields per case type** are returned in `required_elements` from `POST /questions`. Use `sections_display` from intake responses to render a structured view.

**Statutes of limitations** (flag in UI when relevant):
- `personal_injury` / `property_damage`: 3 years (CPLR § 214)
- `employment_dispute` (wages): 3 years; EEOC charge: 300 days
- `contract_dispute` (written): 6 years (CPLR § 213)
- `eminent_domain`: 1 year + 90 days — **flag immediately, urgent**
- `family_law`: no SOL on divorce

---

## 9. Error Reference

| HTTP Status | Meaning | Common cause |
|---|---|---|
| `400` | Bad request | Invalid `case_type` in `/intake/start` |
| `401` | Unauthorized | Missing `Authorization` header on protected route |
| `403` | Forbidden | Wrong Bearer token |
| `404` | Not found | Unknown `session_id` or `case_id` |
| `413` | Payload too large | Input text > 5,000 chars |
| `422` | Unprocessable entity | Blank question / text; or draft blocked by missing fields |
| `429` | Too many requests | Rate limit hit (30 req/min on `/questions`) |
| `503` | Service unavailable | Claude API down or ANTHROPIC_API_KEY missing |
| `500` | Internal server error | Unexpected error — check server logs |

**422 from `POST /draft/:id`** when fields are missing returns a structured body:
```json
{
  "detail": {
    "message": "Cannot generate complaint — 2 required field(s) are missing...",
    "missing_count": 2,
    "fields": [
      { "field_id": "injury_description", "question": "What injuries did the plaintiff suffer?" }
    ]
  }
}
```

---

## 10. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | — | Anthropic API key. All AI calls fail without this. |
| `API_KEY` | No (prod: yes) | `""` | Bearer token for protected routes. Empty = auth disabled. |
| `ALLOWED_ORIGINS` | No | `http://localhost:3000,...` | Comma-separated CORS origins |
| `BACKEND_URL` | No | `http://localhost:9000` | Consumed by `backend_client.py` (Streamlit only) |
| `DB_PATH` | No | `chat_history.db` | SQLite file path |

**Minimum `.env` for local dev:**
```
ANTHROPIC_API_KEY=sk-ant-...
```

**Production `.env`:**
```
ANTHROPIC_API_KEY=sk-ant-...
API_KEY=your-secret-bearer-token
ALLOWED_ORIGINS=https://yourapp.com
```

---

## Appendix — Startup Command Reference

```bash
# Development (port 8000, all interfaces)
uvicorn app:app --host "::" --port 8000

# Development with auto-reload
uvicorn app:app --host "::" --port 8000 --reload

# Production (original port, single worker)
python app.py           # starts on 0.0.0.0:9000
```
