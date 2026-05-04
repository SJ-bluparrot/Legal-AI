# 2026-04-17 — Unified Case Loop: Full Day's Work

> **Status at end of day:** All changes documented here were reverted to yesterday's pushed state (`3be06af` / `origin/feature/nyaay-streamlit-chat`). This document preserves the full context so the work can be re-implemented cleanly.

---

## 1. What We Were Building

The "unified case loop" — a sequential pipeline on every `/questions` message that:

1. Detects case type from the very first user query (not just at intake)
2. Extracts case entities (plaintiff, defendant, incident details, etc.) continuously across turns
3. Asks for only the fields required for that specific case type's complaint
4. Rewrites the raw SaulLM answer via Claude as a licensed NY attorney, with statute validation

Key design principle: **start extracting from turn 1, never ask again for what was already provided**.

---

## 2. The Full 7-Step Pipeline (implemented in `app.py` → `post_question()`)

```
User query
  ↓
Step 1: generate_answer()          — SaulLM raw legal analysis
  ↓
Step 2: classify_case()            — SaulLM classifies case type (+ confidence)
  ↓
Step 3: extract_elements()         — static lookup: which fields are required for this case type?
  ↓
Step 4: extract_entities()         — SaulLM pulls entity values from conversation so far
         + is_valid_extracted_value() filter  ← filters vague extractions like "my client"
  ↓
Step 5: upsert_case_session_from_chat()  — DB merge: new fields win, accumulate across turns
  ↓
Step 6: compute missing_elements   — required fields not yet in DB
  ↓
Step 7: claude_rewrite()           — Claude rewrites as NY attorney, asks for missing fields
```

All SaulLM GPU calls use `asyncio.to_thread()` to avoid blocking the async FastAPI event loop.

---

## 3. Files Changed

### `app.py` (most changes)

**Deleted:**
- `LEGAL_INTENT_SIGNALS` keyword list (~30 legal terms)
- `has_legal_intent()` function — brittle keyword matching

**Added:**
- `classify_legal_intent(question, model, tokenizer) -> bool` — SaulLM yes/no prompt
  - Uses `<s>[INST]...[/INST]` Mistral format
  - Fails open (returns `True`) on exception so real legal queries are never blocked
  - Prompt: "Does this describe a legal situation or case? Answer yes or no."

- `upsert_case_session_from_chat(chat_session_id, case_type, required_elements, new_provided_fields) -> dict`
  - Creates `case_sessions` row on first `/questions` call (no need for `/intake/start` first)
  - Merges fields across turns (new non-empty values overwrite old ones)
  - Strips `__sources__` key before merging
  - Returns `{"provided_fields": dict, "missing_fields": list}`

- `build_rewrite_prompt(raw_answer, question, case_type, is_low_confidence, required_elements, known_fields, missing_elements) -> str`
  - **UNCERTAIN mode** (case_type == "other" OR is_low_confidence == True):
    - Give 2-3 sentence legal overview
    - Ask 2-3 clarifying questions to determine case type
  - **FIELD COLLECTION mode** (case type known, high confidence):
    - Write 3-4 sentence legal reasoning relevant to the query
    - Validate/correct any statutes from raw analysis
    - List missing required fields conversationally: "To move forward, could you provide..."

**Rewired `post_question()`** — replaced parallel `asyncio.gather()` with sequential 7-step pipeline (classification result must feed rewrite prompt).

**Removed from `build_prompt()`** (SaulLM system prompt):
- `STATUTE ACCURACY RULES` block that suppressed statute citations
- Hard-coded statute examples
- Replaced with: "Cite specific NY statutes and CPLR sections whenever they apply. A legal AI will validate all citations."

### `validator.py`

Added public function:
```python
def is_valid_extracted_value(field_id: str, value: str) -> bool:
    """Return True if entity-extracted value passes field-level validation."""
    if not value or not str(value).strip():
        return False
    ok, _ = _dispatch_field_check(field_id, str(value).strip())
    return ok
```

Used in `post_question()` to filter vague extractions before DB merge.

### `classifier.py`

No net change — original classification prompt was kept as-is (trusting SaulLM's legal training rather than adding hard-coded disambiguation rules). Key function signature:

```python
def classify_case(question: str, model, tokenizer) -> tuple[str, bool]:
    # Returns (case_type, is_low_confidence)
    # is_low_confidence = True when top token probability < 0.40
```

### `utils.py`

- `normalize_case_fields()`: `"[UNKNOWN]"` → `"____________"` (12 underscores)
- `build_court_caption()`: all sentinel guards updated from `"[UNKNOWN]"` to `"____________"`
- Added `timezone` to module-level datetime import

### `complaint_drafter.py`

- `_system_prompt()`: rules 4 and 6 updated — `[UNKNOWN]` → `____________`
- All 7 case-type template f-strings: `.get('field', '[UNKNOWN]')` → `.get('field', '____________')`
- `_count_unknowns()`: `re.findall(r'\[UNKNOWN\]', text)` → `text.count("____________")`
- Removed dead `import re`

### `styles.py`

Added CSS reset for nested `stHorizontalBlock` to fix purple box bleed:

```css
[data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
[data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
[data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:not(:first-child):not(:last-child) {
    background: transparent !important;
    min-height: auto !important;
    padding: revert !important;
    border-right: none !important;
    overflow-y: visible !important;
    position: static !important;
}
```

**Root cause:** CSS `:first-child`/`:last-child` selectors in `styles.py` matched nested column layouts inside `_render_generate_area()`, causing the purple panel background to bleed into inner columns.

### New Test Files

| File | Tests |
|------|-------|
| `tests/test_blank_placeholder.py` | 4 tests — `____________` substitution in normalize + drafter |
| `tests/test_rewrite_prompt.py` | 8 tests — `build_rewrite_prompt()` UNCERTAIN vs FIELD COLLECTION modes |
| `tests/test_upsert_case_session.py` | 4 tests — upsert create/merge/overwrite/no-overwrite-with-empty |
| `tests/test_ny_filter.py` | Fixed `test_car_repair_blocked` → `test_car_repair_passes_jurisdiction_filter` (assertion flipped) |

---

## 4. Bugs Found and Fixed

### Bug 1: Purple Box in Streamlit UI
- **Symptom:** A large purple box appeared in the middle of the chat UI
- **Root cause:** CSS `:first-child`/`:last-child` selectors matched nested `stHorizontalBlock` from `_render_generate_area()`
- **Fix:** Added CSS reset rule targeting nested stColumn elements inside stColumn

### Bug 2: `[UNKNOWN]` vs `____________` Sentinel Mismatch
- **Symptom:** Code reviewer caught that `build_court_caption()` still checked for `"[UNKNOWN]"` after `normalize_case_fields()` was changed to emit `"____________"`
- **Fix:** Updated all sentinel guards in `build_court_caption()`

### Bug 3: Vague Entity Extraction
- **Symptom:** SaulLM extracted "my client" → `plaintiff_name`, "Manhattan store" → `defendant_name`. These passed the non-empty check, so they were stored as "known" fields, artificially reducing missing field count.
- **Fix:** Added `is_valid_extracted_value()` filter using `_dispatch_field_check()` from `validator.py`. "my client" fails name validation (lowercase "my"), "Manhattan store" fails (lowercase "store").

### Bug 4: Intent Gate Blocking Valid Legal Queries
- **Symptom:** "rear-ended" not in keyword list → blocked as non-legal
- **Initial bad fix:** Add "rear-ended" to keyword list
- **Correct fix:** Delete entire keyword list + `has_legal_intent()`. Replace with SaulLM yes/no classification that understands legal context without explicit keywords.

### Bug 5: `tempfile.mktemp()` Deprecated
- **Location:** `tests/test_upsert_case_session.py`
- **Fix:** `tempfile.mkstemp()` with `os.close(fd)`

### Bug 6: Redundant `uuid` Import
- **Location:** `upsert_case_session_from_chat()` function
- **Fix:** Removed local import, added `timezone` to module-level datetime import

### Bug 7: Wrong Test Assertion (test_car_repair_blocked)
- **Symptom:** Test asserted `ny_filter` should block "How do I fix my car engine?" but "car" is in the allowlist to cover car accident cases. Non-legal intent is the intent gate's job, not the jurisdiction filter's.
- **Fix:** Renamed test to `test_car_repair_passes_jurisdiction_filter`, flipped assertion to `blocked is False`

---

## 5. Key Architectural Decisions

### Decision 1: SaulLM for intent classification, not keywords
- **Rejected:** Expanding keyword list (brittle, never complete, "rear-ended" incident)
- **Chosen:** SaulLM yes/no prompt — model understands legal context natively from training
- **Tradeoff:** Extra GPU inference per request, but eliminates maintenance burden

### Decision 2: Sequential pipeline, not parallel
- **Rejected:** `asyncio.gather(classify, rewrite)` — rewrite can't use classification result
- **Chosen:** Sequential 7-step pipeline — classification feeds both extract_elements and rewrite_prompt
- **Tradeoff:** Higher latency, but correct context in every step

### Decision 3: Trust SaulLM legal training, no hard-coded rules
- **Rejected:** Hard-coded disambiguation rules in classifier prompt (e.g., "if theft → property_damage not criminal_defense")
- **Rejected:** Hard-coded statute examples in rewrite prompt
- **Chosen:** Clean prompts, trust the model's domain training
- **Why:** User feedback: "i can see you are hard coding everything why my model cant understand the scenario"

### Decision 4: Entity extraction from turn 1, continuous accumulation
- **Rejected:** Wait for case type detection before extracting entities
- **Chosen:** Extract everything from every turn, merge into DB, case type + entities converge together
- **Why:** Attorney provides details in every message — stupid to ask same question twice after case type detected

### Decision 5: `____________` instead of `[UNKNOWN]` in drafts
- **Why:** User preference — underscores look like blank lines in a legal form, more professional

---

## 6. What Was NOT Implemented (Deferred)

- `generate_answer()` is a blocking sync call in async endpoint — pre-existing, not regressed
- `extract_elements()` called synchronously — acceptable, static lookup with no GPU
- Comparative negligence statutes and specific SOL numbers — trusting Claude's NY attorney training
- Force generation button (attorney can request complaint without all fields) — in backlog
- Streaming responses — in backlog
- Sidebar case summary panel — in backlog

---

## 7. Re-implementation Priority Order

When re-implementing, do these in order to avoid dependency issues:

1. **`utils.py`** — `[UNKNOWN]` → `____________` sentinel change (no dependencies)
2. **`complaint_drafter.py`** — sentinel change + `_count_unknowns()` fix (depends on utils)
3. **`validator.py`** — add `is_valid_extracted_value()` public function (no dependencies)
4. **`styles.py`** — purple box CSS fix (no dependencies)
5. **`app.py`** — the big one: delete intent gate, add `classify_legal_intent()`, add `upsert_case_session_from_chat()`, add `build_rewrite_prompt()`, rewire `post_question()`
6. **Tests** — add new test files, fix `test_car_repair_blocked`

---

## 8. The Prompt Templates (exact text)

### `classify_legal_intent()` prompt
```
You are a legal intake classifier for a New York law firm assistant.
Determine if the following query describes a legal situation, case, or legal question
that an attorney might handle.

Answer with exactly one word — yes or no.

Examples:
- "My client was rear-ended on the highway" → yes
- "The city seized my property for a highway project" → yes
- "How do I fix my car engine?" → no
- "What is the best recipe for pasta?" → no
- "My employer fired me without cause" → yes

Query: "{question}"

Answer with exactly one word — yes or no:
```

### `build_rewrite_prompt()` — UNCERTAIN mode
```
You are a licensed New York attorney assistant...
The legal classifier could not confidently determine the case type for this query.

Your task:
1. Write 2-3 sentences of legal context relevant to the question (what area of law this might touch, 
   what the attorney should consider).
2. Ask 2-3 focused clarifying questions to help identify the specific legal matter and case type.

Do NOT ask for fields like plaintiff name, defendant name, or dates yet — focus on 
understanding the nature of the legal dispute first.
```

### `build_rewrite_prompt()` — FIELD COLLECTION mode
```
You are a licensed New York attorney assistant specializing in {case_type} cases...

KNOWN INFORMATION:
{known_fields}

REQUIRED FIELDS STILL MISSING:
{missing_fields}

Your task:
1. Write 3-4 sentences of legal reasoning relevant to this query and case type.
   Reference specific NY law or CPLR sections if cited in the raw analysis.
2. STATUTE VALIDATION — for every statute cited in the raw analysis:
   - Citation is correct → keep it exactly.
   - Section number is wrong but statute name is right → correct the section number.
   - Statute does not exist or is wrong for this case type → replace with doctrine name only.
   - Do NOT add statute citations that were not in the raw analysis.
3. If there are missing required fields, ask for them conversationally:
   "To move forward, could you provide the following?"
   [list missing fields with brief explanations]
4. If all required fields are collected, say: "I have all the information needed to draft the complaint."
```

---

## 9. Git State at End of Day

- **Last pushed commit:** `3be06af` (yesterday, `origin/feature/nyaay-streamlit-chat`)
- **Local commits above that:** 15 commits, none pushed (git push was killed per user instruction)
- **Action taken:** `git reset --hard origin/feature/nyaay-streamlit-chat` — all local commits discarded

All the implementation above needs to be re-done on top of the reverted base.
