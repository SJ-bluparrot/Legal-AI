# Nyaay Frontend Integration Guide

> Backend: FastAPI on `http://localhost:9000`  
> All API paths are relative — if the frontend is served from the same origin (e.g. `GET /` returns `ui.html`), no CORS configuration is needed.

---

## 1. How the backend works end-to-end

Every user message goes through a two-call pipeline inside `/questions`:

1. **Haiku conversational call** — generates the attorney-facing response text
2. **Haiku extraction call** — silently parses the conversation and returns structured JSON: `case_type`, `extracted_fields`, `missing_fields`, `ready_to_draft`

The frontend never calls the extraction call directly — it just reads the flags returned by `/questions`.

---

## 2. State machine your UI must implement

```
CHAT_ONLY
  │
  │  offer_complaint = true returned by /questions
  ▼
INTAKE_ACTIVE  ◄──────────────────────────────────────────────┐
  │                                                            │
  │  Every user message: call /questions AND                   │
  │  /intake/{case_id}/provide in parallel                     │
  │                                                            │
  │  is_complete = true returned by /intake/{case_id}/provide  │
  ▼                                                            │
READY_TO_DRAFT                                                 │
  │                                                            │
  │  User clicks "Generate Complaint"                          │
  │  (or PATCH /intake/{case_id}/force if fields missing)      │
  ▼                                                            │
DRAFTING                                                       │
  │  POST /draft/{case_id}                                     │
  ▼                                                            │
DRAFT_COMPLETE                                                 │
  │  Show complaint text + DOCX/PDF download buttons           │
  │                                                            │
  │  User starts a new chat → reset state ──────────────────────
```

**Critical:** When state is `INTAKE_ACTIVE`, `READY_TO_DRAFT`, `DRAFTING`, or `DRAFT_COMPLETE`, skip any client-side NY jurisdiction filtering. Short answers like "yes", "I don't know", "no insurance" will be incorrectly blocked by keyword matching.

---

## 3. API calls in order

### Step 1 — First message (no session yet)

```js
const res = await fetch('/questions', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question: userText, session_id: null })
});
const data = await res.json();

// Always save these
sessionId = data.session_id;      // string UUID
caseType  = data.case_type;       // e.g. "personal_injury"

// Render the AI response
appendMessage('assistant', data.answer);

// When this flips to true, start intake
if (data.offer_complaint) {
  await startIntake(sessionId, caseType, userText);
}
```

### Step 2 — All follow-up messages

```js
const res = await fetch('/questions', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question: userText, session_id: sessionId })
});
const data = await res.json();
appendMessage('assistant', data.answer);

// If intake is already active, pipe the message through intake in parallel
if (intakeActive && caseId) {
  await provideToIntake(caseId, userText);
}

// Check if we just crossed the threshold
if (data.offer_complaint && !intakeActive) {
  await startIntake(sessionId, data.case_type, userText);
}
```

### Step 3 — Start intake (call once)

```js
async function startIntake(sessionId, caseType, initialText) {
  intakeActive = true;

  const res = await fetch('/intake/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, case_type: caseType, initial_text: initialText })
  });
  const data = await res.json();
  caseId = data.case_id;  // save this globally

  updateProgressUI(data);   // show which fields are filled vs missing

  if (data.is_complete) {
    flowState = 'READY_TO_DRAFT';
  }
}
```

### Step 4 — Provide each follow-up message to intake

```js
async function provideToIntake(caseId, text) {
  const res = await fetch(`/intake/${caseId}/provide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });
  const data = await res.json();

  updateProgressUI(data);

  if (data.is_complete) {
    flowState = 'READY_TO_DRAFT';
    enableGenerateButton();
  }
}
```

### Step 5 — Generate the complaint

```js
async function generateComplaint() {
  flowState = 'DRAFTING';
  showSpinner();

  const res = await fetch(`/draft/${caseId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  });

  if (res.status === 422) {
    // Fields still missing — offer force draft
    const err = await res.json();
    showForceWarning(err.detail.fields);   // list of missing field questions
    return;
  }

  const data = await res.json();
  flowState = 'DRAFT_COMPLETE';
  showDraft(data.complaint, data.word_count, data.unknown_count);
  enableDownloadButtons();
}
```

### Step 6 — Force draft (if fields still missing)

```js
async function forceDraft() {
  await fetch(`/intake/${caseId}/force`, { method: 'PATCH' });
  await generateComplaint();   // retry — will now succeed
}
```

### Step 7 — Download DOCX / PDF

```js
async function downloadDocx(attorneyName = '') {
  const res = await fetch(`/document/${caseId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attorney_name: attorneyName })
  });
  const blob = await res.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `complaint_${caseId.slice(0, 8)}.docx`;
  a.click();
  URL.revokeObjectURL(url);
}

async function downloadPdf(attorneyName = '') {
  const res = await fetch(`/document/${caseId}/pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attorney_name: attorneyName })
  });
  const blob = await res.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `complaint_${caseId.slice(0, 8)}.pdf`;
  a.click();
  URL.revokeObjectURL(url);
}
```

---

## 4. Session sidebar

```js
// Load all sessions on startup
async function loadSessions() {
  const res  = await fetch('/sessions');
  const list = await res.json();
  renderSessionList(list);  // each item: { id, title, created_at }
}

// When user clicks a session
async function openSession(id) {
  const res  = await fetch(`/history/${id}`);
  const msgs = await res.json();
  // Each item: { role: "user"|"assistant", content, timestamp }
  renderMessages(msgs);
  sessionId = id;
}

// Delete a session
async function deleteSession(id) {
  await fetch(`/sessions/${id}`, { method: 'DELETE' });
  await loadSessions();  // refresh list
}
```

---

## 5. Progress bar (optional)

Poll `GET /case/{case_id}/progress` to drive a visual progress indicator.

```js
async function pollProgress() {
  const res  = await fetch(`/case/${caseId}/progress`);
  const data = await res.json();
  // data.percent: 0–100
  // data.provided_fields: { field_id: "value", ... }
  // data.missing_fields: ["field_id", ...]
  setProgressBar(data.percent);
}
```

---

## 6. Full API reference (quick-lookup)

| Method | Path | When to call |
|--------|------|-------------|
| `GET` | `/health` | Startup check |
| `GET` | `/` | Served automatically — the HTML frontend |
| `POST` | `/questions` | Every user message |
| `GET` | `/sessions` | Sidebar load |
| `GET` | `/history/{session_id}` | Restore a previous session |
| `POST` | `/history/{session_id}/clear` | Clear messages (keep session) |
| `DELETE` | `/sessions/{session_id}` | Delete session permanently |
| `POST` | `/intake/start` | Once, when `offer_complaint` first becomes true |
| `POST` | `/intake/{case_id}/provide` | Every user message after intake starts |
| `GET` | `/intake/{case_id}` | Page reload — restore intake state |
| `GET` | `/intake/validate/{case_id}` | On-demand field validation check |
| `PATCH` | `/intake/{case_id}/force` | User confirms proceed with missing fields |
| `GET` | `/case/{case_id}/progress` | Progress bar polling |
| `POST` | `/draft/{case_id}` | Generate complaint (idempotent — cached after first call) |
| `GET` | `/draft/{case_id}` | Restore cached draft on page reload |
| `POST` | `/document/{case_id}` | Download DOCX |
| `POST` | `/document/{case_id}/pdf` | Download PDF |
| `GET` | `/document/{case_id}/status` | Check if draft is ready to export |

---

## 7. Key field names and types

### `/questions` request
```json
{
  "question":   "string (max 2000 chars)",
  "session_id": "string UUID | null"
}
```

### `/questions` response
```json
{
  "answer":            "string",
  "session_id":        "string UUID",
  "offer_complaint":   true,
  "case_type":         "personal_injury | employment_dispute | contract_dispute | property_damage | eminent_domain | criminal_defense | family_law | other | unsupported",
  "required_elements": [{ "id": "string", "label": "string", "description": "string" }],
  "sections":          {}
}
```

### `/intake/start` request
```json
{
  "session_id":   "string UUID (from /questions)",
  "case_type":    "personal_injury | ...",
  "initial_text": "string (user's last message or case summary)"
}
```

### `/intake/start` + `/intake/{id}/provide` response shape
```json
{
  "case_id":        "string UUID",
  "case_type":      "string",
  "is_complete":    false,
  "pre_filled":     { "field_id": "value", ... },
  "missing_required": [{ "id": "field_id", "label": "Field Label", "section": "Section Name" }],
  "missing_optional": [...],
  "sections_display": {
    "Section Name": {
      "fields": [{ "id": "field_id", "label": "string", "value": "string|null", "filled": true, "required": true }]
    }
  },
  "validation": { "valid": true, "issues": [] }
}
```

### `/draft/{case_id}` response
```json
{
  "case_id":       "string UUID",
  "case_type":     "string",
  "complaint":     "string (full complaint text, 500–1500 words)",
  "from_cache":    false,
  "word_count":    912,
  "unknown_count": 1
}
```

---

## 8. Error codes

| Code | Meaning | What to do |
|------|---------|-----------|
| `422` on `/draft` | Required fields still missing | Show `detail.fields` questions, offer Force Draft button |
| `503` on `/draft` | Claude API unavailable | Show retry button |
| `404` on `/history`, `/draft`, `/document` | Resource not found | Session or case expired — start fresh |
| `429` on `/questions` | Rate limit (30 req/min per IP) | Show "slow down" message |
| `400` on `/intake/start` | Invalid `case_type` | Supported types: personal_injury, employment_dispute, contract_dispute, property_damage, eminent_domain, criminal_defense, family_law |

---

## 9. Page reload / session restore checklist

On load, in order:
1. `GET /sessions` — populate sidebar
2. If a `session_id` is stored (localStorage/URL param):
   - `GET /history/{session_id}` — restore messages
   - `GET /intake/{case_id}` (if `case_id` stored) — restore intake state
   - `GET /draft/{case_id}` (if `case_id` stored) — restore draft if generated (200 = show it, 404 = not yet)

---

## 10. Supported case types

| API value | Display label |
|-----------|--------------|
| `personal_injury` | Personal Injury |
| `employment_dispute` | Employment Dispute |
| `contract_dispute` | Contract Dispute |
| `property_damage` | Property Damage |
| `eminent_domain` | Eminent Domain |
| `criminal_defense` | Criminal Defense |
| `family_law` | Family Law |
| `other` | Other (no complaint generation available) |
| `unsupported` | Outside NY jurisdiction (blocked) |
