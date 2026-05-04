# Unified Case Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/questions` endpoint run entity extraction on every message from message 1, inject detected case type + known/missing fields into the Claude rewrite prompt, validate SaulLM statute citations through Claude as a NY attorney, lead every response with 2-4 lines of case context, and replace `[UNKNOWN]` with `____________` in all complaint drafts.

**Architecture:** Classification runs sequentially before the Claude rewrite so its result can be injected into the rewrite prompt. Entity extraction (SaulLM) then runs sequentially after classification. The Claude rewrite is the last step, now receiving case_type + known_fields + missing_fields so it can operate in two modes: clarifying-questions mode (case type uncertain) and field-collection mode (case type confirmed). A new `upsert_case_session_from_chat()` helper auto-creates/updates the `case_sessions` DB row on every `/questions` call, accumulating extracted fields across turns without requiring the attorney to manually start an intake session.

**Tech Stack:** Python 3.10, FastAPI, SQLite (via `get_db_connection()`), SaulLM-7B (via `classify_case`, `extract_entities`), Claude Haiku (`claude-haiku-4-5-20251001` via `anthropic` SDK), `asyncio.to_thread` for blocking GPU calls.

---

## File Map

| File | What changes |
|------|-------------|
| `utils.py` | `normalize_case_fields()` — replace `"[UNKNOWN]"` literal with `"____________"` |
| `complaint_drafter.py` | `_system_prompt()`, all 7 case-type templates, `_count_unknowns()` — replace `[UNKNOWN]` with `____________` throughout |
| `app.py` | (1) Remove statute suppression from Saul-7B system prompt. (2) Move `_build_rewrite_prompt` to module-level, give it new signature with case context. (3) Add `upsert_case_session_from_chat()`. (4) Rewire `ask_question_endpoint()` — sequential classify → extract_elements → extract_entities → upsert → rewrite. |
| `tests/test_blank_placeholder.py` | New — tests for `____________` substitution in normalize_case_fields |
| `tests/test_rewrite_prompt.py` | New — tests for `build_rewrite_prompt()` two-mode logic |
| `tests/test_upsert_case_session.py` | New — tests for upsert create/merge/update logic |

---

## Task 1: Replace `[UNKNOWN]` with `____________` in utils.py

**Files:**
- Modify: `utils.py:108`
- Test: `tests/test_blank_placeholder.py` (new)

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_blank_placeholder.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils import normalize_case_fields

BLANK = "____________"

def test_missing_required_field_becomes_blank():
    provided  = {"plaintiff_name": "John Doe"}
    required  = ["plaintiff_name", "defendant_name"]
    result    = normalize_case_fields(provided, required)
    assert result["defendant_name"] == BLANK

def test_present_required_field_is_unchanged():
    provided  = {"plaintiff_name": "John Doe", "incident_date": "Jan 5 2023"}
    required  = ["plaintiff_name", "incident_date"]
    result    = normalize_case_fields(provided, required)
    assert result["plaintiff_name"] == "John Doe"
    assert result["incident_date"]  == "Jan 5 2023"

def test_blank_not_unknown():
    provided = {}
    required = ["plaintiff_name"]
    result   = normalize_case_fields(provided, required)
    assert "[UNKNOWN]" not in result["plaintiff_name"]
    assert result["plaintiff_name"] == BLANK

def test_empty_string_required_field_becomes_blank():
    provided = {"plaintiff_name": "   "}
    required = ["plaintiff_name"]
    result   = normalize_case_fields(provided, required)
    assert result["plaintiff_name"] == BLANK
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
conda run -n saul_env python -m pytest tests/test_blank_placeholder.py -v
```

Expected: `FAILED — AssertionError` because `normalize_case_fields` still returns `"[UNKNOWN]"`.

- [ ] **Step 1.3: Fix utils.py**

In `utils.py` line 108, change:

```python
# OLD
normalized[field_id] = value.strip() if value and value.strip() else "[UNKNOWN]"
```

to:

```python
# NEW
normalized[field_id] = value.strip() if value and value.strip() else "____________"
```

Also update the docstring examples in `utils.py` lines 89–91 from `"[UNKNOWN]"` to `"____________"` so documentation stays accurate:

```python
        #     "defendant_name":    "____________",   ← missing required
        #     "incident_location": "____________",   ← missing required
```

- [ ] **Step 1.4: Run test to verify it passes**

```bash
conda run -n saul_env python -m pytest tests/test_blank_placeholder.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 1.5: Commit**

```bash
git add utils.py tests/test_blank_placeholder.py
git commit -m "fix: replace [UNKNOWN] with ____________ in normalize_case_fields"
```

---

## Task 2: Replace `[UNKNOWN]` in complaint_drafter.py

**Files:**
- Modify: `complaint_drafter.py` — `_system_prompt()`, all 7 case-type template functions, `_count_unknowns()`

No new tests needed — the existing complaint drafter tests and the Task 1 tests cover the behaviour. This task is a pure mechanical substitution.

- [ ] **Step 2.1: Update `_system_prompt()` in complaint_drafter.py**

Find the instruction at line ~161:

```python
# OLD
"4. Where a field contains '[UNKNOWN]', write [UNKNOWN] exactly as-is in the document. "
"Do NOT invent, guess, or paraphrase missing information. "
"The attorney will replace [UNKNOWN] with the real value before filing.\n"
"5. Do not add any commentary, explanation, or notes outside the complaint document itself. "
"Output ONLY the complaint text.\n"
"6. The complaint should be complete and court-ready in structure, even if some "
"field values are [UNKNOWN].\n"
```

Replace with:

```python
# NEW
"4. Where a field contains '____________', write ____________ exactly as-is in the document. "
"Do NOT invent, guess, or paraphrase missing information. "
"The attorney will fill in the blanks before filing.\n"
"5. Do not add any commentary, explanation, or notes outside the complaint document itself. "
"Output ONLY the complaint text.\n"
"6. The complaint should be complete and court-ready in structure, even if some "
"field values are ____________.\n"
```

- [ ] **Step 2.2: Replace `[UNKNOWN]` in all 7 case-type template f-strings**

Run this find-and-replace across complaint_drafter.py (every `.get('field_id', '[UNKNOWN]')` becomes `.get('field_id', '____________')`):

```bash
sed -i "s/'\[UNKNOWN\]'/'____________'/g" /home/ubuntu/saul_project/SaulLM-AI/complaint_drafter.py
```

- [ ] **Step 2.3: Fix `_count_unknowns()` function**

Find `_count_unknowns` in complaint_drafter.py and replace:

```python
# OLD
def _count_unknowns(text: str) -> int:
    """Count how many [UNKNOWN] placeholders remain in the draft."""
    return len(re.findall(r'\[UNKNOWN\]', text))
```

```python
# NEW
def _count_unknowns(text: str) -> int:
    """Count how many ____________ placeholders remain in the draft."""
    return text.count("____________")
```

- [ ] **Step 2.4: Verify no `[UNKNOWN]` remains in complaint_drafter.py**

```bash
grep -n "\[UNKNOWN\]" /home/ubuntu/saul_project/SaulLM-AI/complaint_drafter.py
```

Expected: no output (zero matches).

- [ ] **Step 2.5: Smoke-test that complaint_drafter still imports cleanly**

```bash
conda run -n saul_env python -c "from complaint_drafter import build_draft_prompt; print('OK')"
```

Expected: `OK`

- [ ] **Step 2.6: Commit**

```bash
git add complaint_drafter.py
git commit -m "fix: replace [UNKNOWN] with ____________ in complaint_drafter templates"
```

---

## Task 3: Remove statute suppression from Saul-7B system prompt

**Files:**
- Modify: `app.py` lines 843–846 (the `STATUTE ACCURACY RULES` block in `build_prompt()`)

The goal is to let SaulLM cite statutes freely. Claude will validate them in Task 4.

- [ ] **Step 3.1: Delete the STATUTE ACCURACY RULES block from app.py**

Find and remove these 4 lines (currently around line 843–846):

```python
# REMOVE THIS ENTIRE BLOCK:
        "STATUTE ACCURACY RULES:\n"
        "- Only cite a specific statute (e.g., NYLL § 215, CPLR § 214) if you are certain it is correct.\n"
        "- If uncertain of the exact section number, use the statute NAME only (e.g., 'the New York Labor Law retaliation provision') without a section number.\n"
        "- Never cite a general statute (e.g., NY General Obligations Law) as the basis for tort doctrines like negligence or attractive nuisance — these are common law claims in New York, not statutory.\n\n"
```

Also find and remove the duplicate statute caution in the `LEGAL ACCURACY RULES` block around line 860–863:

```python
# REMOVE THESE TWO LINES (keep the surrounding context):
        "- If you are certain of a statute, cite it correctly.\n"
        "- If you are NOT certain, do NOT invent placeholder statutes (e.g., 'Section XYZ').\n"
        "- Instead, refer generally to 'applicable state law' or 'general tort law'.\n\n"
```

Replace the entire `LEGAL ACCURACY RULES` block with a single encouraging line:

```python
        "LEGAL ACCURACY RULES:\n"
        "- Cite specific NY statutes and CPLR sections whenever they apply (e.g., CPLR § 214, NYLL § 215, NY Penal Law § 120.00). "
        "A legal AI reviewing your output will validate all citations before delivery.\n\n"
```

- [ ] **Step 3.2: Verify app.py still imports cleanly**

```bash
conda run -n saul_env python -c "import app; print('OK')"
```

Expected: `OK` (model won't load in a bare import check — that's fine, just checking parse errors).

- [ ] **Step 3.3: Commit**

```bash
git add app.py
git commit -m "feat: allow SaulLM to cite statutes freely — Claude will validate during rewrite"
```

---

## Task 4: Refactor `_build_rewrite_prompt` into a module-level function with NY attorney persona

**Files:**
- Modify: `app.py` — extract the nested `_build_rewrite_prompt` from `ask_question_endpoint` into a module-level function `build_rewrite_prompt` with new signature
- Test: `tests/test_rewrite_prompt.py` (new)

- [ ] **Step 4.1: Write the failing tests**

Create `tests/test_rewrite_prompt.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# We test the pure prompt-building function — no model needed.
from app import build_rewrite_prompt

PERSONAL_INJURY_ELEMENTS = [
    {"id": "plaintiff_name",     "label": "Plaintiff Name",       "required": True},
    {"id": "defendant_name",     "label": "Defendant Name",       "required": True},
    {"id": "incident_date",      "label": "Date of Incident",     "required": True},
    {"id": "incident_location",  "label": "Location of Incident", "required": True},
    {"id": "injury_description", "label": "Description of Injury","required": True},
    {"id": "negligence_act",     "label": "Negligent Act",        "required": True},
]


def test_uncertain_mode_asks_clarifying_questions():
    prompt = build_rewrite_prompt(
        raw_answer="The user may have a legal claim.",
        question="My client has a problem.",
        case_type="other",
        is_low_confidence=True,
        required_elements=[],
        known_fields={},
        missing_elements=[],
    )
    assert "clarifying" in prompt.lower() or "identify" in prompt.lower()
    assert "New York attorney" in prompt


def test_confirmed_mode_includes_case_type():
    prompt = build_rewrite_prompt(
        raw_answer="Negligence applies here under CPLR 214.",
        question="My client was hit by a car.",
        case_type="personal_injury",
        is_low_confidence=False,
        required_elements=PERSONAL_INJURY_ELEMENTS,
        known_fields={"plaintiff_name": "John Doe"},
        missing_elements=[e for e in PERSONAL_INJURY_ELEMENTS if e["id"] != "plaintiff_name"],
    )
    assert "personal_injury" in prompt or "Personal Injury" in prompt
    assert "John Doe" in prompt


def test_confirmed_mode_lists_missing_fields():
    missing = [
        {"id": "defendant_name", "label": "Defendant Name", "required": True},
        {"id": "incident_date",  "label": "Date of Incident", "required": True},
    ]
    prompt = build_rewrite_prompt(
        raw_answer="The client has a personal injury claim.",
        question="My client was hurt.",
        case_type="personal_injury",
        is_low_confidence=False,
        required_elements=PERSONAL_INJURY_ELEMENTS,
        known_fields={"plaintiff_name": "John Doe"},
        missing_elements=missing,
    )
    assert "Defendant Name" in prompt
    assert "Date of Incident" in prompt


def test_no_missing_fields_shows_ready_message():
    prompt = build_rewrite_prompt(
        raw_answer="Strong negligence claim.",
        question="My client John Doe was hit by Jane Smith on Jan 5 on Broadway, fractured wrist, driver ran red light.",
        case_type="personal_injury",
        is_low_confidence=False,
        required_elements=PERSONAL_INJURY_ELEMENTS,
        known_fields={e["id"]: "value" for e in PERSONAL_INJURY_ELEMENTS},
        missing_elements=[],
    )
    assert "Generate Complaint" in prompt or "all required" in prompt.lower() or "ready" in prompt.lower()


def test_statute_validation_instruction_present():
    prompt = build_rewrite_prompt(
        raw_answer="Under CPLR § 214 the SOL is 3 years.",
        question="My client was injured.",
        case_type="personal_injury",
        is_low_confidence=False,
        required_elements=PERSONAL_INJURY_ELEMENTS,
        known_fields={},
        missing_elements=PERSONAL_INJURY_ELEMENTS,
    )
    assert "statute" in prompt.lower() or "citation" in prompt.lower()
    assert "New York" in prompt
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
conda run -n saul_env python -m pytest tests/test_rewrite_prompt.py -v
```

Expected: `ImportError: cannot import name 'build_rewrite_prompt' from 'app'`

- [ ] **Step 4.3: Add `build_rewrite_prompt` as a module-level function in app.py**

Remove the nested `_build_rewrite_prompt` function from inside `ask_question_endpoint` (lines ~1152–1184). Add this module-level function before the `ask_question_endpoint` definition (around line 1055, just above the `@app.post` decorator):

```python
# ──────────────────────────────────────────────
# Claude rewrite prompt builder — module-level so it is testable.
# ──────────────────────────────────────────────
def build_rewrite_prompt(
    raw_answer: str,
    question: str,
    case_type: str,
    is_low_confidence: bool,
    required_elements: list[dict],
    known_fields: dict,
    missing_elements: list[dict],
) -> str:
    """
    Build the prompt sent to Claude Haiku for the rewrite step.

    Two modes:
      - CLARIFYING: case_type is 'other' or is_low_confidence=True.
        Claude asks 2-3 targeted questions to identify the case type.
        Still opens with 1-2 sentences of context.
      - FIELD COLLECTION: case_type is known and confident.
        Claude opens with 2-4 lines of case context, rewrites the analysis
        in structured format, validates SaulLM statute citations as a NY
        attorney, and lists only the missing required fields.

    Args:
        raw_answer        : SaulLM's raw legal reasoning output.
        question          : The attorney's original message.
        case_type         : Classifier result (e.g. "personal_injury").
        is_low_confidence : True when classifier confidence < threshold.
        required_elements : Full element schema for the detected case type.
        known_fields      : Field values already extracted from conversation.
        missing_elements  : Required elements not yet in known_fields.
    """
    uncertain = (case_type in ("other", "unsupported") or is_low_confidence)

    if uncertain:
        return (
            "You are a licensed New York attorney reviewing a new client intake query.\n\n"
            "The case type could not be confidently identified from the information provided.\n\n"
            "Write a SHORT response with two parts:\n"
            "1. Open with 1-2 sentences of context about what the facts suggest so far "
            "(e.g. possible theories, who appears to be the injured party, what kind of harm is described).\n"
            "2. Then ask 2-3 numbered clarifying questions that would help identify the specific "
            "case type. Good examples: 'Was your client physically injured?', "
            "'Is your client the one being accused, or are they the victim?', "
            "'Did this involve an employment or workplace situation?'\n\n"
            "Rules:\n"
            "- Do NOT write a full legal analysis.\n"
            "- Do NOT ask for intake form fields yet — only ask what is needed to identify the case type.\n"
            "- Maximum 100 words total.\n"
            "- Write for a licensed attorney. Never say 'consult a lawyer'.\n\n"
            f"Original query: {question}\n\n"
            f"Raw analysis: {raw_answer}"
        )

    # ── FIELD COLLECTION MODE ──────────────────────────────────────────────
    case_type_human = case_type.replace("_", " ").title()

    # Format known fields for the prompt (only show populated ones)
    if known_fields:
        clean_known = {k: v for k, v in known_fields.items()
                       if v and str(v).strip() and k != "__sources__"}
        known_str = ", ".join(f"{k}: {v}" for k, v in clean_known.items()) if clean_known else "None yet"
    else:
        known_str = "None yet"

    # Format missing fields as human-readable labels
    if missing_elements:
        missing_labels = "\n".join(f"- {e['label']}" for e in missing_elements)
        missing_section = (
            f"**To draft the complaint, I still need:**\n{missing_labels}"
        )
    else:
        missing_section = (
            "**Ready to draft** — all required information has been collected. "
            "Click \"Generate Complaint\" to proceed."
        )

    return (
        "You are a licensed New York attorney reviewing a legal analysis written by a junior associate.\n\n"
        f"CASE CONTEXT:\n"
        f"- Detected case type: {case_type_human}\n"
        f"- Already collected from conversation: {known_str}\n"
        f"- Missing required fields: "
        + (", ".join(e["label"] for e in missing_elements) if missing_elements else "None — all collected")
        + "\n\n"
        "YOUR TASK:\n"
        "1. Open with 2-4 sentences of case-specific context: what legal theory applies, "
        "why the facts support it, and any directly relevant New York law principle "
        "(e.g. applicable statute of limitations, governing NY statute or CPLR section).\n\n"
        "2. Rewrite the analysis using this exact structure:\n\n"
        f"**Case Type:** {case_type_human}\n\n"
        "**Legal Overview**\n"
        "2-3 sentences on the directly applicable NY claims — only those supported by the stated facts.\n\n"
        "**Key Elements to Prove**\n"
        "Bullet list of 3-5 elements the attorney must establish.\n\n"
        "**What Your Client Can Do**\n"
        "Bullet list of 3-5 concrete next steps written for the attorney.\n\n"
        "**Possible Recovery**\n"
        "1-2 sentences on available remedies under NY law.\n\n"
        f"{missing_section}\n\n"
        "3. STATUTE VALIDATION — for every statute cited in the raw analysis:\n"
        "   - Citation is correct → keep it exactly.\n"
        "   - Section number is wrong but statute name is right → correct the section number.\n"
        "   - Statute does not exist or is completely wrong → replace with the doctrine name only.\n"
        "   - Never invent new statute citations yourself.\n\n"
        "Rules:\n"
        "- Write for a licensed New York attorney — never say 'consult a lawyer'.\n"
        "- Maximum 3 legal theories, only those directly supported by the stated facts.\n"
        "- Keep total response under 300 words.\n\n"
        f"Raw analysis to rewrite:\n{raw_answer}\n\n"
        f"Original query: {question}"
    )
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
conda run -n saul_env python -m pytest tests/test_rewrite_prompt.py -v
```

Expected: 5 tests PASSED.

- [ ] **Step 4.5: Commit**

```bash
git add app.py tests/test_rewrite_prompt.py
git commit -m "feat: build_rewrite_prompt as testable module-level function with NY attorney persona and two modes"
```

---

## Task 5: Add `upsert_case_session_from_chat()` to app.py

**Files:**
- Modify: `app.py` — new helper function added after `get_case_session()`
- Test: `tests/test_upsert_case_session.py` (new)

This function is called from `/questions` on every message. It creates the `case_sessions` row the first time a case type is detected for a chat session, and merges new entity-extracted fields into the existing row on subsequent calls.

- [ ] **Step 5.1: Write the failing tests**

Create `tests/test_upsert_case_session.py`:

```python
import sys, os, json, uuid, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Point the DB to a temp file for isolated testing
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")

import app as _app
_app.init_db()   # create tables in the temp DB

from app import upsert_case_session_from_chat

ELEMENTS = [
    {"id": "plaintiff_name",    "label": "Plaintiff Name",    "required": True},
    {"id": "defendant_name",    "label": "Defendant Name",    "required": True},
    {"id": "incident_date",     "label": "Date of Incident",  "required": True},
    {"id": "witness_names",     "label": "Witness Names",     "required": False},
]


def _new_session_id():
    """Create a real sessions row so FK constraint is satisfied."""
    from app import get_db_connection
    sid = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title) VALUES (?, ?)",
            (sid, "test"),
        )
    return sid


def test_creates_new_row_on_first_call():
    sid    = _new_session_id()
    result = upsert_case_session_from_chat(
        chat_session_id=sid,
        case_type="personal_injury",
        required_elements=ELEMENTS,
        new_provided_fields={"plaintiff_name": "John Doe"},
    )
    assert result["provided_fields"]["plaintiff_name"] == "John Doe"
    assert "defendant_name" in result["missing_fields"]
    assert "incident_date"  in result["missing_fields"]
    # Optional field not in missing_fields
    assert "witness_names"  not in result["missing_fields"]


def test_merges_fields_on_second_call():
    sid = _new_session_id()
    upsert_case_session_from_chat(
        chat_session_id=sid,
        case_type="personal_injury",
        required_elements=ELEMENTS,
        new_provided_fields={"plaintiff_name": "John Doe"},
    )
    result = upsert_case_session_from_chat(
        chat_session_id=sid,
        case_type="personal_injury",
        required_elements=ELEMENTS,
        new_provided_fields={"defendant_name": "Mark Smith", "incident_date": "Jan 5 2023"},
    )
    pf = result["provided_fields"]
    assert pf["plaintiff_name"]  == "John Doe"    # from turn 1
    assert pf["defendant_name"]  == "Mark Smith"  # from turn 2
    assert pf["incident_date"]   == "Jan 5 2023"  # from turn 2
    assert result["missing_fields"] == []          # all required fields now filled


def test_new_field_overwrites_old_on_conflict():
    sid = _new_session_id()
    upsert_case_session_from_chat(
        chat_session_id=sid,
        case_type="personal_injury",
        required_elements=ELEMENTS,
        new_provided_fields={"plaintiff_name": "Old Name"},
    )
    result = upsert_case_session_from_chat(
        chat_session_id=sid,
        case_type="personal_injury",
        required_elements=ELEMENTS,
        new_provided_fields={"plaintiff_name": "Corrected Name"},
    )
    assert result["provided_fields"]["plaintiff_name"] == "Corrected Name"


def test_empty_new_fields_preserves_existing():
    sid = _new_session_id()
    upsert_case_session_from_chat(
        chat_session_id=sid,
        case_type="personal_injury",
        required_elements=ELEMENTS,
        new_provided_fields={"plaintiff_name": "John Doe"},
    )
    result = upsert_case_session_from_chat(
        chat_session_id=sid,
        case_type="personal_injury",
        required_elements=ELEMENTS,
        new_provided_fields={},
    )
    assert result["provided_fields"]["plaintiff_name"] == "John Doe"
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
conda run -n saul_env python -m pytest tests/test_upsert_case_session.py -v
```

Expected: `ImportError: cannot import name 'upsert_case_session_from_chat' from 'app'`

- [ ] **Step 5.3: Add `upsert_case_session_from_chat()` to app.py**

Add this function just after the `get_case_session()` function (around line 810):

```python
def upsert_case_session_from_chat(
    chat_session_id: str,
    case_type: str,
    required_elements: list[dict],
    new_provided_fields: dict,
) -> dict:
    """
    Create or update a case_sessions row from the /questions endpoint.

    Called on every attorney message so entity-extracted fields accumulate
    across turns without requiring the attorney to manually start an intake
    session. On the first call for a chat session it creates a new row.
    On subsequent calls it merges new_provided_fields into the existing row
    (new values win on conflict — lets attorneys correct prior extractions).

    Args:
        chat_session_id    : The chat session ID from the sessions table.
        case_type          : Detected case type string.
        required_elements  : Full element schema list from extract_elements().
        new_provided_fields: Fields extracted from the current message only.

    Returns:
        {
            "provided_fields": dict,   # merged all-turns field values
            "missing_fields":  list,   # required field IDs still empty
        }
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    all_field_ids      = [e["id"] for e in required_elements]
    required_field_ids = [e["id"] for e in required_elements if e.get("required")]

    now = datetime.now(timezone.utc).isoformat()

    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM case_sessions WHERE chat_session_id = ? ORDER BY rowid DESC LIMIT 1",
            (chat_session_id,),
        ).fetchone()

        if existing:
            # Merge existing + new (new wins on conflict)
            merged = json.loads(existing["provided_fields"])
            merged.pop("__sources__", None)
            merged.update({k: v for k, v in new_provided_fields.items() if v and str(v).strip()})

            missing_field_ids = [
                fid for fid in required_field_ids
                if not str(merged.get(fid, "")).strip()
            ]

            conn.execute(
                """UPDATE case_sessions
                   SET case_type = ?, provided_fields = ?,
                       missing_fields = ?, required_fields = ?, updated_at = ?
                   WHERE case_id = ?""",
                (
                    case_type,
                    json.dumps(merged),
                    json.dumps(missing_field_ids),
                    json.dumps(all_field_ids),
                    now,
                    existing["case_id"],
                ),
            )
        else:
            case_id = str(_uuid.uuid4())
            merged  = {k: v for k, v in new_provided_fields.items() if v and str(v).strip()}

            missing_field_ids = [
                fid for fid in required_field_ids
                if not str(merged.get(fid, "")).strip()
            ]

            conn.execute(
                """INSERT INTO case_sessions
                   (case_id, chat_session_id, case_type,
                    required_fields, provided_fields, missing_fields,
                    force_draft, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    case_id, chat_session_id, case_type,
                    json.dumps(all_field_ids),
                    json.dumps(merged),
                    json.dumps(missing_field_ids),
                    now, now,
                ),
            )

    return {
        "provided_fields": merged,
        "missing_fields":  missing_field_ids,
    }
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
conda run -n saul_env python -m pytest tests/test_upsert_case_session.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5.5: Commit**

```bash
git add app.py tests/test_upsert_case_session.py
git commit -m "feat: add upsert_case_session_from_chat — auto-accumulates entity fields from /questions across turns"
```

---

## Task 6: Rewire `ask_question_endpoint()` — sequential pipeline + inject context

**Files:**
- Modify: `app.py` — `ask_question_endpoint()` (lines ~1139–1266)

This is the main wiring task. It changes the execution order from parallel (classify + rewrite) to sequential (classify → extract_elements → extract_entities → upsert → rewrite with full context injected).

- [ ] **Step 6.1: Replace the pipeline block in `ask_question_endpoint()`**

Find the block starting at `# Step 1 — Generate legal answer` (around line 1139) through to the `return QuestionResponse(...)` call. Replace the entire block with:

```python
    # Step 1 — Generate raw legal answer (blocking GPU call)
    prompt = build_prompt(request_data.question, previous_messages)
    answer = generate_answer(prompt)

    # Step 2 — Classify case type (sequential; result needed before entity extraction)
    # Fast: max_new_tokens=10, greedy decoding. Runs on same GPU, now free after generate.
    def _classify():
        return classify_case(request_data.question, model, tokenizer)

    case_type, classification_low_confidence = await asyncio.to_thread(_classify)

    # Step 3 — Get required element schema for detected case type (instant, no GPU)
    elements_result   = extract_elements(case_type, model, tokenizer)
    required_elements = elements_result.get("elements", [])
    sections          = elements_result.get("sections", {})
    all_element_ids   = [e["id"] for e in required_elements]

    # Step 4 — Extract entities from current message (sequential GPU call)
    # Regex layer runs first (no GPU, instant); SaulLM only for fields regex missed.
    def _extract():
        return extract_entities(
            text=request_data.question,
            case_type=case_type,
            required_element_ids=all_element_ids,
            model=model,
            tokenizer=tokenizer,
        )

    new_fields, _ = await asyncio.to_thread(_extract)

    # Step 5 — Upsert case session: merge new_fields with prior turns in DB
    session_state = upsert_case_session_from_chat(
        chat_session_id=session_id,
        case_type=case_type,
        required_elements=required_elements,
        new_provided_fields=new_fields,
    )
    all_known   = session_state["provided_fields"]
    missing_ids = set(session_state["missing_fields"])

    # Step 6 — Build missing_elements list for the rewrite prompt
    missing_elements = [e for e in required_elements if e.get("required") and e["id"] in missing_ids]

    # Step 7 — Claude rewrite (async HTTP) — receives full case context
    async def _claude_rewrite() -> str:
        if not ANTHROPIC_API_KEY:
            return answer
        try:
            import anthropic as _anthropic
            _client  = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            prompt_text = build_rewrite_prompt(
                raw_answer=answer,
                question=request_data.question,
                case_type=case_type,
                is_low_confidence=classification_low_confidence,
                required_elements=required_elements,
                known_fields=all_known,
                missing_elements=missing_elements,
            )
            msg = await asyncio.to_thread(
                _client.messages.create,
                model="claude-haiku-4-5-20251001",
                max_tokens=700,
                messages=[{"role": "user", "content": prompt_text}],
            )
            return msg.content[0].text if msg.content else answer
        except Exception as e:
            logger.warning(f"Claude rewrite failed: {e} — returning raw SaulLM answer")
            return answer

    rewritten_answer = await _claude_rewrite()

    # Persist messages
    with get_db_connection() as conn:
        save_message(conn, session_id, "user",      request_data.question)
        save_message(conn, session_id, "assistant", rewritten_answer)

    # Set session title from first message
    if not previous_messages:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (request_data.question[:50], session_id),
            )

    logger.info(
        f"Done | session={session_id} | case_type={case_type} "
        f"| low_confidence={classification_low_confidence} "
        f"| known={len(all_known)} fields | missing={len(missing_ids)} required"
    )

    offer_complaint = (
        case_type in COMPLAINT_SUPPORTED_CASES
        and not classification_low_confidence
    )

    return QuestionResponse(
        answer=rewritten_answer,
        session_id=session_id,
        offer_complaint=offer_complaint,
        case_type=case_type,
        required_elements=required_elements,
        sections=sections,
        classification_low_confidence=classification_low_confidence,
    )
```

- [ ] **Step 6.2: Add `extract_entities` to imports at top of `ask_question_endpoint` scope**

Verify `entity_extractor` is already imported at the module level of `app.py`:

```bash
grep "from entity_extractor import\|import entity_extractor" /home/ubuntu/saul_project/SaulLM-AI/app.py
```

If not present, add to the imports block near the top of `app.py`:

```python
from entity_extractor import extract_entities
```

- [ ] **Step 6.3: Verify app.py parses cleanly**

```bash
conda run -n saul_env python -c "
import ast, sys
with open('app.py') as f:
    src = f.read()
try:
    ast.parse(src)
    print('Parse OK')
except SyntaxError as e:
    print(f'Syntax error: {e}')
    sys.exit(1)
"
```

Expected: `Parse OK`

- [ ] **Step 6.4: Run the full existing test suite**

```bash
conda run -n saul_env python -m pytest tests/ -v --tb=short
```

Expected: all previously passing tests still pass. New tests from Tasks 1, 4, 5 also pass.

- [ ] **Step 6.5: Restart the backend and do a live smoke test**

```bash
# Kill existing backend if running
pkill -f "python app.py" 2>/dev/null || true
sleep 2

# Start backend
cd /home/ubuntu/saul_project/SaulLM-AI
conda run -n saul_env python app.py &
sleep 15

# Smoke test 1: first message, case type uncertain
curl -s -X POST http://localhost:9000/questions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: Saul_Lm-BluParrot124" \
  -d '{"question": "My client has a problem at work"}' | python3 -m json.tool | grep -E "case_type|offer_complaint|answer" | head -20

# Smoke test 2: first message with enough facts for personal injury
curl -s -X POST http://localhost:9000/questions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: Saul_Lm-BluParrot124" \
  -d '{"question": "My client John Doe was rear-ended by a driver on Broadway on March 5th 2024. He suffered a fractured wrist."}' | python3 -m json.tool | grep -E "case_type|offer_complaint" | head -10
```

Expected for test 1: `case_type` is `"other"` or similar, `offer_complaint` is `false`, answer contains clarifying questions.
Expected for test 2: `case_type` is `"personal_injury"`, answer opens with case context before asking for missing fields.

- [ ] **Step 6.6: Commit**

```bash
git add app.py
git commit -m "feat: rewire ask_question_endpoint — sequential classify→extract→upsert→rewrite with full case context injection"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Entity extraction from message 1, every message | Task 6 (Step 6.1 — `_extract()` called in pipeline) |
| Auto-create case_sessions row from /questions | Task 5 (`upsert_case_session_from_chat`) |
| Accumulate fields across turns (merge not overwrite) | Task 5 (merge logic in upsert) |
| case_type injected into rewrite prompt | Task 4 (`build_rewrite_prompt` signature + Task 6 call) |
| known_fields injected into rewrite prompt | Task 4 + Task 6 |
| missing_fields injected into rewrite prompt | Task 4 + Task 6 |
| Clarifying mode when case type uncertain | Task 4 (uncertain branch in `build_rewrite_prompt`) |
| Field collection mode when case type confirmed | Task 4 (confirmed branch) |
| Only ask for fields not yet mentioned (smart gap) | Task 4 (`missing_elements` = required - known) |
| SaulLM cites statutes freely | Task 3 (remove suppression from system prompt) |
| Claude validates statutes as NY attorney | Task 4 (STATUTE VALIDATION section in confirmed mode) |
| 2-4 line case context before asking questions | Task 4 (both modes open with context) |
| `[UNKNOWN]` → `____________` in complaint drafts | Tasks 1 + 2 |

**No placeholders found** — all steps contain complete code.

**Type consistency:**
- `build_rewrite_prompt` defined in Task 4 with signature `(raw_answer, question, case_type, is_low_confidence, required_elements, known_fields, missing_elements)` — called in Task 6 with identical argument names. ✅
- `upsert_case_session_from_chat` defined in Task 5, called in Task 6 with matching arg names. ✅
- `extract_entities` returns `(values: dict, sources: dict)` — Task 6 unpacks as `new_fields, _`. ✅
- `"____________"` literal used consistently in Tasks 1 and 2, never `"[UNKNOWN]"`. ✅
