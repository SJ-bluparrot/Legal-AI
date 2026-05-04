# Nyaay AI — Haiku Conversational Layer + SaulLM Complaint Drafter
**Date:** 2026-04-18
**Status:** Approved — ready to implement

---

## Problem

SaulLM-7B is being used as a conversational assistant, but it was designed for legal document understanding. This causes:
- Hallucinated legal scenarios for vague/off-topic input
- No ability to ask clarifying questions
- Unreliable case type classification (temperature=0.7)
- ~30-40% real quality pass rate despite 100% HTTP pass rate

## Solution

Split responsibilities by model strength:

- **Claude Haiku** — all conversation, classification, entity extraction
- **SaulLM** — only complaint drafting (given a complete structured brief)

---

## Architecture

```
PHASE 1 — Every message to /questions

  User message + conversation history + DB state
           ↓
  Call 1: Haiku CONVERSE
    → Natural response (greet / legal brief / captured so far / questions)
           ↓
  Call 2: Haiku EXTRACT
    → { case_type, extracted_fields, missing_fields, ready_to_draft }
           ↓
  Merge into case_sessions DB
  Return answer + offer_complaint flag to frontend


PHASE 2 — Attorney confirms → /draft/{case_id}

  Build structured brief from DB (all collected fields)
           ↓
  SaulLM generates complaint sections
  (Caption, Parties, Facts, Claims, Wherefore)
           ↓
  Haiku cleanup pass (fix formatting, catch errors)
           ↓
  Final complaint → attorney
```

---

## Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| State tracking | Pass DB state into every Haiku call | Haiku needs accurate state, not implied from history |
| Extraction | Separate call (not inline) | Reliable JSON extraction, no format mixing |
| IRAC analysis | Inline brief paragraph only | Attorneys know the law; 2-3 sentences of legal context is enough |
| Draft trigger | Explicit confirm + force draft option | Never auto-trigger without attorney confirmation |
| SaulLM input | Structured brief | Easier to verify, easier to debug |

---

## Haiku Converse — Response Format

Every response follows this structure:
1. Acknowledge what attorney told you (1-2 sentences)
2. ONE paragraph legal brief when case type first identified
   (case type, applicable law, SOL — nothing more)
3. **Captured so far:** bullet list of extracted fields
4. Up to 4 clarifying questions (most critical first)
5. When all required fields collected → "I have everything needed. Shall I proceed?"

---

## Haiku Extract — Output Schema

```json
{
  "case_type": "personal_injury | employment_dispute | criminal_defense | contract_dispute | property_damage | eminent_domain | family_law | unknown",
  "extracted_fields": {
    "field_name": "value"
  },
  "missing_fields": ["field_name"],
  "ready_to_draft": false
}
```

---

## SaulLM Structured Brief Format

```
Case Type: Personal Injury — Motor Vehicle Negligence
Jurisdiction: New York

PARTIES
Plaintiff: John Doe
Defendant: John Doe, operator of black Toyota (plate USD 345)

INCIDENT
Date: April 17, 2026
Time: 1:00 PM
Location: Walmart parking lot, [address]

FACTS
Injuries: [injuries]
Medical costs: $[amount]
Lost wages: $[amount]
Witnesses: [witnesses]

INSTRUCTIONS
Draft a formal NY CPLR complaint with these sections:
1. Caption
2. Jurisdictional Statement
3. Parties
4. Factual Allegations
5. Causes of Action
6. Wherefore Clause
```

---

## Implementation Tasks

### Phase 1 — Haiku Conversational Layer (app.py)

**Task 1** — Write Haiku converse system prompt
- Role definition, response format, field schemas for all 7 case types
- Legal tips: John Doe/FOIA, SOL warnings, surveillance 72h window
- Jurisdiction block, off-topic handling, force draft handling

**Task 2** — `_haiku_converse(question, history, case_state)` *(blocked by Task 1)*
- Calls Haiku API with conversation history + injected DB state
- Returns conversational response string

**Task 3** — `_haiku_extract(recent_turns)` *(blocked by Task 1)*
- Separate Haiku call, JSON extraction only
- Returns structured extraction dict
- Includes retry/fallback if JSON parse fails

**Task 4** — Rewrite `/questions` endpoint *(blocked by Tasks 2 + 3)*
- Load session + history + case_state from DB
- Call converse → save messages → call extract → merge DB → return response
- Remove SaulLM calls and GPU lock from this endpoint

**Task 5** — Field merge + case type switching *(blocked by Task 4)*
- `merge_case_extraction()`: never overwrite with null values
- Handle mid-conversation case type change: reload required_fields, keep common fields

**Task 6** — Remove dead code *(blocked by Tasks 4 + 5)*
- Delete: `classify_case()` call, `build_prompt()`, `_claude_rewrite()`, GPU lock in `/questions`
- Keep: `generate_answer()` — still needed for SaulLM complaint drafting

### Phase 2 — SaulLM Complaint Drafter (complaint_drafter.py)

**Task 7** — `build_complaint_brief()` function
- Takes case_session dict from DB
- Normalizes missing fields to [UNKNOWN] via `normalize_case_fields()`
- Returns structured brief string ready for SaulLM

**Task 8** — Modify `complaint_drafter.py` *(blocked by Task 7)*
- SaulLM generates complaint → Haiku cleanup pass → save to `draft_text`
- Keep same `/draft/{case_id}` endpoints and DB columns

### Phase 3 — Testing

**Task 9** — Stress test Phase 1 *(blocked by Task 6)*
- All 49 scenarios: greetings, vague, legal, off-topic, wrong jurisdiction
- Verify quality responses, not just HTTP 200

**Task 10** — End-to-end complaint generation test *(blocked by Tasks 8 + 9)*
- Walk full flow: chat intake → confirm → SaulLM draft → Haiku cleanup
- Test force_draft with missing fields + [UNKNOWN] placeholders

---

## Files Changed

| File | Change |
|------|--------|
| `app.py` | Major — new Haiku functions, rewritten `/questions` endpoint, dead code removed |
| `complaint_drafter.py` | Modified — SaulLM primary drafter + Haiku cleanup |

## Files Untouched

`intake_router.py`, `complaint_router.py`, `validator.py`, `element_extractor.py`, `docx_router.py`, `utils.py`
