# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Running the backend
```bash
python app.py
# Server starts at http://localhost:9000
# Swagger docs at http://localhost:9000/docs
```

### Running the Streamlit frontend
```bash
streamlit run streamlit_app.py
# Defaults to http://localhost:8501
```

### Running tests
```bash
pytest tests/
pytest tests/test_ny_filter.py          # single test file
pytest tests/test_backend_client.py
```

### Environment setup
```bash
conda env create -f saul_env.yml
conda activate saul_env
pip install -r requirements.txt
```

PyTorch must be installed separately with CUDA 12.1 for the L4 GPU:
```bash
pip install torch==2.2.0 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### Required `.env` file
```
ANTHROPIC_API_KEY=sk-ant-...
API_KEY=your_backend_api_key       # leave empty in dev to skip auth
BACKEND_URL=http://localhost:9000  # consumed by backend_client.py
ALLOWED_ORIGINS=http://localhost:3000
```

## Architecture

This is a two-process system: a **FastAPI backend** (`app.py`) and a **Streamlit frontend** (`streamlit_app.py`). They communicate via HTTP through `backend_client.py`.

### AI model split

Two models handle distinct roles — never conflate them:
- **Saul-7B-Instruct** (`Equall/Saul-7B-Instruct-v1`): loaded at startup in 8-bit quantization on GPU. Handles IRAC legal analysis, case classification, and entity extraction. Uses Mistral `[INST]...[/INST]` + `<<SYS>>` prompt format.
- **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`): called via Anthropic API for conversational attorney responses (`_haiku_converse`) and structured JSON field extraction (`_haiku_extract`) on every `/questions` request.
- **Claude Sonnet**: called via Anthropic API by `complaint_drafter.py` to generate the final formal complaint document.

### Request flow for `/questions`

1. Frontend NY filter (`ny_filter.py`) — instant keyword rejection, no API call
2. Backend jurisdiction check (`app.py:is_unsupported_jurisdiction`) — server-side enforcement
3. `_haiku_converse()` — Claude Haiku generates the attorney-facing conversational response
4. Messages persisted to SQLite
5. `_haiku_extract()` — second Haiku call returns structured JSON (case_type + extracted_fields)
6. `_upsert_case_session()` — merges extracted fields into `case_sessions` table
7. Returns `offer_complaint=True` when MVP fields are met (triggers Generate button in UI)

### Intake flow (silent background collection)

The Streamlit frontend never shows a form — field collection happens silently through chat. When `offer_complaint=True` is returned from `/questions`, the frontend calls `/intake/start`, then pipes every subsequent user message through `/intake/{case_id}/provide`. The intake router (`intake_router.py`) runs entity extraction and updates the `case_sessions` table incrementally.

### Database schema (`chat_history.db`)

SQLite with WAL mode. Three tables:
- `sessions` — chat session records (id, title, created_at)
- `messages` — all chat turns (session_id FK, role, content, timestamp)
- `case_sessions` — intake state (case_id PK, chat_session_id FK, case_type, required_fields JSON, provided_fields JSON, missing_fields JSON, force_draft, draft_generated, draft_text)

Schema migrations are applied idempotently at startup via `ALTER TABLE ADD COLUMN` with swallowed duplicate-column errors. Any new column must be added to both the `CREATE TABLE` statement and the `migrations` list in `init_db()`.

### Jurisdiction filtering (two layers)

Both `ny_filter.py` (frontend) and `app.py` (backend) implement the same 3-step allowlist logic:
1. NY signal found → ALLOW
2. Non-NY US state found → BLOCK
3. Foreign signal found → BLOCK
4. Default (no geographic signal) → ALLOW (assume New York)

The NY check runs first so "new york" is never shadowed by "new jersey" in the other-states list. The `ny_filter.py` frontend version adds an extra legal-intent check to block off-topic non-legal queries before the geography check.

### NY filter bypass during active intake

In `streamlit_app.py:handle_user_message`, the frontend NY filter is intentionally skipped when `flow_state` is `INTAKE_ACTIVE`, `READY_TO_DRAFT`, `DRAFTING`, or `DRAFT_COMPLETE`. Short follow-up answers like "yes", "I have insurance", or "I don't know" would otherwise be rejected as non-legal.

### GPU memory management

The backend is tuned for a single NVIDIA L4 (24GB VRAM). Key constraints:
- Hard VRAM cap: 14GiB for Saul-7B (leaves ~10GB for other GPU tasks)
- `_gpu_lock` (asyncio.Lock) serializes all `model.generate()` calls — GPU is not thread-safe
- `del output_ids, inputs` + `torch.cuda.empty_cache()` after every inference call
- TF32 enabled for faster matrix multiply on Ada Lovelace: `torch.backends.cuda.matmul.allow_tf32 = True`

### Draft lock

If `draft_generated = 1` in `case_sessions`, `complaint_drafter.py` returns the cached `draft_text` immediately without calling Claude. This prevents duplicate API charges on retries and page reloads.

### `[UNKNOWN]` handling

`utils.normalize_case_fields()` replaces missing required fields with `[UNKNOWN]` before building the Claude complaint prompt. Claude is explicitly instructed to use `[UNKNOWN]` as-is — never invent values for missing fields.

### Streamlit flow states

The frontend tracks `flow_state` in `st.session_state`:
- `CHAT_ONLY` → Generate button disabled
- `INTAKE_ACTIVE` → intake running silently, button active but may show missing-field warning
- `READY_TO_DRAFT` → all MVP fields collected, button fully active
- `DRAFTING` → spinner shown
- `DRAFT_COMPLETE` → complaint displayed with DOCX/PDF download buttons

### MVP fields (minimum viable facts to offer a draft)

Defined in `app.py:_MVP_FIELDS`. Intentionally minimal — complaints can be filed with partial info and `[UNKNOWN]` placeholders. The `offer_complaint` flag is only set when all MVP fields for the detected case type are present in `provided_fields`.

### Supported case types

`personal_injury`, `employment_dispute`, `contract_dispute`, `property_damage`, `eminent_domain`, `criminal_defense`, `family_law`. The `"other"` classification has no element schema and cannot produce a complaint.

### Rate limiting

`slowapi` limits `/questions` to 30 req/minute per IP. Auth middleware protects `/intake/*` and `/draft/*` with a Bearer token (skipped entirely if `API_KEY` env var is empty).
