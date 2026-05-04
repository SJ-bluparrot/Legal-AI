# Nyaay AI — Project Progress & Roadmap

> Last updated: 2026-04-16  
> Tested via: Playwright against http://localhost:8501 and http://localhost:8502  
> Backend: http://localhost:9000 (Saul-7B on NVIDIA L4, 8-bit quantization)

---

## System Status

| Component | Status | Notes |
|-----------|--------|-------|
| Backend (FastAPI) | ✅ Healthy | PID 258783, port 9000, model loaded |
| Saul-7B model | ✅ Loaded | 8-bit quant, 7.17GB VRAM used, 14.86GB free |
| Streamlit frontend | ✅ Running | Port 8501 (old process) + 8502 (current) |
| SQLite session DB | ✅ Working | Sessions persisting across browser visits |
| ANTHROPIC_API_KEY | ✅ Set | Via .env file |
| API_KEY auth | ✅ Set | `Saul_Lm-BluParrot124` via .env |
| NY jurisdiction filter | ✅ Passing | 24/24 tests pass |
| Backend client | ✅ Passing | 19/19 tests pass |

---

## What Was Built (Complete)

### Core Pipeline
- **SaulLM-AI FastAPI backend** (`app.py`, 1331 lines) — full legal AI pipeline:
  - Multi-turn chat with SQLite session persistence
  - NY-first jurisdiction filter (3-step: NY allow → non-NY block → foreign block → assume NY)
  - Legal intent gate (blocks non-legal queries before hitting GPU)
  - Greeting handler (no GPU inference for greetings)
  - SaulLM-7B inference with Mistral [INST] format
  - Claude Haiku rewrite pass on every SaulLM answer (cleaner attorney-facing output)
  - Async parallel: Claude rewrite + case classification run concurrently (saves ~1–2s)
  - Case classifier → `offer_complaint` + `case_type` in every response
  - Intake loop (`/intake/start`, `/provide`, `/force`) for field collection
  - Entity extractor (regex + SaulLM, 3-layer) auto-fills intake fields from prose
  - Field validator with per-case-type rules
  - Complaint drafter using Claude Sonnet (per-case-type prompts, draft lock)
  - DOCX + PDF export
  - `/sessions` and `/history/{id}` endpoints for session management

### Frontend (Streamlit)
- **`streamlit_app.py`** — ChatGPT/Claude-style UI:
  - Static two-column layout (left panel permanent, never collapses)
  - Left panel: "Recent chats" list from live DB, "+ New Chat" button
  - Skeleton shimmer loading screen (replaces generic spinner)
  - Two-phase message rendering (user message appears instantly, response in skeleton)
  - NY filter applied frontend before any API call
  - Case detection → auto intake → Generate Complaint button activates silently
  - Force-proceed warning when required fields missing
  - `st.code()` for draft display (fixes disabled textarea invisible text bug)
  - DOCX + PDF download buttons

### Supporting Modules
| File | Purpose |
|------|---------|
| `ny_filter.py` | Frontend keyword filter, `check_ny_filter()` |
| `backend_client.py` | All HTTP wrappers, `get_sessions()`, `get_session_history()` |
| `styles.py` | Superhuman-inspired theme (dark purple sidebar + white main) |
| `entity_extractor.py` | Regex + SaulLM field extraction from prose |
| `element_extractor.py` | Required legal elements per case type |
| `classifier.py` | Case type classification (8 types) |
| `complaint_drafter.py` | Claude Sonnet complaint generation with draft lock |
| `complaint_router.py` | `/draft/{case_id}` endpoints |
| `intake_router.py` | Stateful intake loop endpoints |
| `validator.py` | Field-level + SOL validation |
| `docx_generator.py` | DOCX export |
| `pdf_generator.py` | PDF export |

---

## Playwright Test Results (2026-04-16)

### Homepage (8501 — user-facing)
| Check | Result |
|-------|--------|
| Page loads | ✅ |
| Two-column layout renders | ✅ |
| Left panel shows sessions | ✅ (5 sessions visible) |
| "+ New Chat" button present | ✅ |
| "Nyaay AI" header in main area | ✅ |
| "Generate Complaint" button present (disabled) | ✅ |
| Chat input at bottom | ✅ |
| Custom CSS loaded | ❌ (8501 old process didn't hot-reload styles.py) |
| Left panel dark purple background | ❌ (CSS not loaded on 8501) |

### Homepage (8502 — current process)
| Check | Result |
|-------|--------|
| Custom CSS loaded | ✅ (13,446 bytes injected) |
| Left column computed bg = #1b1938 | ✅ |
| Right column computed bg = #ffffff | ✅ |
| Sessions visible | ✅ |

### Known Issues Found in Testing
1. **8501 stale process** — started at 10:36 with `nohup`, file watcher failed to reload `styles.py`. Fix: kill 8501 and restart on that port, or direct users to 8502.
2. **Left panel missing dark theme on 8501** — caused by issue #1. When using 8502 CSS is correct.
3. **Right column y-position offset** — `getBoundingClientRect().y = -486` with `scrollY = 0`. The block-container is positioned such that columns start above the viewport. Content is still visible and functional.

---

## Active Issues (Fix These Next)

### Critical (affects usability)
- [ ] **Kill stale 8501 process and restart** — users sharing the 8501 URL see wrong theme. Run: `kill 138834 && cd ~/saul_project/SaulLM-AI && conda activate saul_env && streamlit run streamlit_app.py --server.port 8501`
- [ ] **Left panel negative y-offset** — columns start at y=-486. Investigate block-container margin/padding causing the offset. Likely fix: remove `padding-top: 0` from `.main .block-container` or add explicit `margin-top: 0`.

### Visual (polish)
- [ ] **Left panel background not applying on stale process** — resolved by killing 8501

---

## Next Steps — From Deferred Improvements

Ordered by impact (highest first):

### 1. Entity extraction in `/questions` (Architecture — HIGH VALUE)
**File:** `app.py` → `ask_question_endpoint()` and `entity_extractor.py`  
**What:** Call `extract_entities()` inside the `/questions` endpoint and pre-seed the intake session with extracted data (plaintiff name, defendant, location, date). Currently entity extraction only happens at `/intake/start`, throwing away structured data already present in the question.  
**Why it matters:** Attorneys describe the full case in their first message. All that structured data gets lost. This closes the biggest gap between backend capability and output quality.  
**Effort:** Medium — 1–2 day implementation.

### 2. Statute of limitations per legal theory (Legal accuracy — HIGH VALUE)
**File:** `app.py` → `build_prompt()` or system prompt  
**What:** Add correct NY filing deadlines to Section 5 of the IRAC response:
- Personal injury: 3 years (CPLR §214)
- Employment / Title VII: 300 days (EEOC) + 3 years NYLL
- Wrongful termination: varies by theory
- Medical malpractice: 2.5 years (CPLR §214-a)  
**Why:** SOL accuracy is table-stakes for attorney-facing product. Wrong deadlines expose attorneys to malpractice liability.  
**Effort:** Low-medium — prompt engineering + validation.

### 3. Pipeline integration gap (Architecture)
**File:** `app.py` → `ask_question_endpoint()`  
**What:** The classifier fires and returns `case_type`, but the response text ignores it. The IRAC answer should acknowledge the detected case type ("Based on the facts you've described, this appears to be a personal injury matter under negligence theory...").  
**Effort:** Low — prompt modification.

### 4. Streaming responses (UI — HIGH POLISH VALUE)
**Files:** `app.py` (SSE endpoint), `streamlit_app.py` (`st.write_stream`)  
**What:** Stream tokens from the model instead of full-dump response. Makes the app feel alive.  
**Effort:** High — requires new streaming endpoint + frontend refactor. Do after architecture fixes.

### 5. Copy response button (UI — Quick win)
**File:** `streamlit_app.py`  
**What:** Per-message clipboard copy button (ChatGPT style) using `st.button` + `st.code` or JS clipboard API.  
**Effort:** Low — 1–2 hours.

### 6. Collapsible IRAC sections (UI)
**File:** `streamlit_app.py`  
**What:** Wrap Facts/Law/Analysis/Remedies in `st.expander()` blocks. Reduces scroll fatigue on long responses.  
**Effort:** Low-medium — requires parsing IRAC structure from response text.

### 7. 4-bit quantization (GPU — Performance)
**File:** `app.py` → `load_model()` → `BitsAndBytesConfig`  
**What:** Switch `load_in_8bit=True` → `load_in_4bit=True` with `bnb_4bit_compute_dtype=torch.float16`. Saves ~2.5GB VRAM (7GB → 4.5GB).  
**Current:** 7.17GB VRAM used / 22.03GB total. Not urgent but frees headroom.  
**Effort:** Low — config change + full regression test across all 8 case types.

### 8. Flash Attention 2 (GPU — Performance)
**File:** `app.py` → `AutoModelForCausalLM.from_pretrained()`  
**What:** Add `attn_implementation="flash_attention_2"`. ~20–30% faster inference on long sequences. Requires `pip install flash-attn` (long compile).  
**Effort:** Low-medium — install + test.

### 9. Replace classifier with DistilBERT (GPU — Performance)
**File:** `classifier.py`  
**What:** Running 7B model for 10-token classification is wasteful. Fine-tune `distilbert-base` on 8 case types. ~300MB VRAM, ~5ms latency vs 2 seconds.  
**Effort:** High — requires labeled training data + fine-tuning pipeline.

### 10. Trim conversation history (GPU — Context quality)
**File:** `app.py` → `MAX_HISTORY_MESSAGES`  
**What:** Currently keeping last 5 message pairs. By turn 3, prompts are 1600+ tokens. Reduce to 2–3 pairs.  
**Effort:** Trivial — change one constant.

---

## Critical Dependency Pins (DO NOT UPGRADE)

| Package | Pin | Reason |
|---------|-----|--------|
| `accelerate` | ==0.31.0 | 1.x calls `model.to()` unconditionally, breaks bitsandbytes 8-bit |
| `bitsandbytes` | ==0.43.3 | 0.44+ uses `set_submodule` which doesn't exist in torch 2.4.0 |
| `transformers` | ==4.42.4 | 5.x breaks bitsandbytes 8-bit loading |

---

## How to Run

```bash
conda activate saul_env
cd ~/saul_project/SaulLM-AI

# Terminal 1 — backend (if not already running)
python app.py

# Terminal 2 — frontend (kill stale 8501 first if needed)
streamlit run streamlit_app.py --server.port 8501
```

- Frontend: http://103.171.97.130:8501  
- Backend docs: http://103.171.97.130:9000/docs
