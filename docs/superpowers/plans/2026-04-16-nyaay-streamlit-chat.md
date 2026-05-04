# Nyaay AI Streamlit Chat Interface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Streamlit chat UI (ChatGPT/Claude-style) for New York attorneys on top of the existing SaulLM-AI FastAPI backend, with auto case detection, field collection, complaint generation, and DOCX/PDF export.

**Architecture:** Multi-file approach — `streamlit_app.py` handles UI only, `backend_client.py` wraps all HTTP calls, `ny_filter.py` provides frontend NY jurisdiction filtering, `styles.py` holds the Nyaay AI CSS theme. One backend file (`app.py`) is modified to replace the US/UK jurisdiction filter with a NY-first filter.

**Tech Stack:** Python, Streamlit, Requests, Pytest, FastAPI (existing backend at port 9000)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `SaulLM-AI/app.py` | Replace US/UK jurisdiction sets + `is_unsupported_jurisdiction()` with NY-first logic |
| Create | `SaulLM-AI/ny_filter.py` | Frontend NY filter — `check_ny_filter(q) -> (blocked: bool, msg: str)` |
| Create | `SaulLM-AI/backend_client.py` | All HTTP calls to the FastAPI backend — one function per endpoint |
| Create | `SaulLM-AI/styles.py` | Nyaay AI CSS theme string — `get_css() -> str` |
| Create | `SaulLM-AI/streamlit_app.py` | UI: chat rendering, session_state, generate button, draft display |
| Create | `SaulLM-AI/tests/test_ny_filter.py` | Unit tests for ny_filter.py |
| Create | `SaulLM-AI/tests/test_backend_client.py` | Unit tests for backend_client.py (mocked HTTP) |
| Modify | `SaulLM-AI/requirements.txt` | Add streamlit, requests, pytest |

---

## Task 1: Update requirements.txt

**Files:**
- Modify: `SaulLM-AI/requirements.txt`

- [ ] **Step 1: Add streamlit, requests, and pytest to requirements.txt**

Open `SaulLM-AI/requirements.txt` and append at the bottom:

```
# Streamlit frontend
streamlit>=1.32.0

# HTTP client — used by backend_client.py to call the FastAPI backend
requests>=2.31.0

# Testing
pytest>=7.4.0
```

- [ ] **Step 2: Install the new dependencies**

```bash
pip install streamlit>=1.32.0 requests>=2.31.0 pytest>=7.4.0
```

Expected: packages install without errors.

- [ ] **Step 3: Verify streamlit is callable**

```bash
streamlit --version
```

Expected: prints a version string like `Streamlit, version 1.32.x`

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
git add requirements.txt
git commit -m "deps: add streamlit, requests, pytest for frontend"
```

---

## Task 2: Modify app.py — NY-first jurisdiction filter

**Files:**
- Modify: `SaulLM-AI/app.py` (lines 225–430 — the jurisdiction filter section)

This replaces the three existing sets (`US_LOCATIONS`, `UK_LOCATIONS`, `FOREIGN_LOCATIONS`) and the `is_unsupported_jurisdiction()` function. Everything else in `app.py` stays untouched.

- [ ] **Step 1: Remove the three existing location sets and the unsupported response string**

Find and delete the block from the comment `# ── US allowlist ──` through `UNSUPPORTED_RESPONSE = (...)` (approximately lines 254–395 in `app.py`). Replace it entirely with the following:

```python
# ── NY allowlist — explicit New York signals ──────────────────────────────────
NY_LOCATIONS = {
    # Boroughs and city names
    "new york", "new york city", "nyc",
    "manhattan", "brooklyn", "queens", "bronx", "staten island",
    # NY state cities and regions
    "long island", "buffalo", "albany", "yonkers", "syracuse",
    "rochester", "white plains", "new rochelle", "mount vernon",
    "schenectady", "utica", "binghamton", "niagara falls",
    "westchester", "nassau", "suffolk",
    # NY courts
    "new york supreme court", "new york court of appeals",
    "southern district of new york", "eastern district of new york",
    "northern district of new york", "western district of new york",
    "sdny", "edny", "ndny", "wdny",
    # NY law and abbreviations
    "new york law", "ny law", "new york state",
    "cplr", "new york penal law", "new york family court",
    "ny penal", "ny civil practice",
}

# ── Non-NY US signals — 49 other states + major non-NY US cities ─────────────
# Checked AFTER NY_LOCATIONS so "new jersey" never shadows "new york".
NON_NY_US_LOCATIONS = {
    # 49 other US states (new york deliberately absent)
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "west virginia", "wisconsin", "wyoming",
    # DC and territories
    "district of columbia", "washington d.c.", "washington dc",
    "puerto rico", "guam", "u.s. virgin islands",
    # Major cities in other states (avoid city names that overlap with NYC neighborhoods)
    "los angeles", "chicago", "houston", "phoenix", "philadelphia",
    "san antonio", "san diego", "dallas", "san jose", "austin",
    "jacksonville", "fort worth", "charlotte", "indianapolis",
    "san francisco", "seattle", "denver", "nashville", "oklahoma city",
    "el paso", "boston", "portland", "las vegas", "memphis", "louisville",
    "baltimore", "milwaukee", "albuquerque", "tucson", "fresno", "mesa",
    "sacramento", "atlanta", "omaha", "colorado springs", "raleigh",
    "miami", "virginia beach", "minneapolis", "tampa", "new orleans",
    "honolulu", "anaheim", "lexington", "henderson",
}

# ── Known foreign signals ─────────────────────────────────────────────────────
# Copied from original FOREIGN_LOCATIONS — no changes needed.
FOREIGN_LOCATIONS = {
    # ── Canada ────────────────────────────────────────────────────────────────
    "canada", "canadian",
    "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
    "toronto", "vancouver", "montreal", "calgary", "edmonton",
    "ottawa", "winnipeg", "halifax", "brampton", "mississauga",
    "criminal code of canada", "canadian charter",
    # ── Australia ─────────────────────────────────────────────────────────────
    "australia", "australian",
    "new south wales", "queensland", "western australia",
    "south australia", "tasmania", "northern territory",
    "australian capital territory",
    "sydney", "melbourne", "brisbane", "perth", "adelaide",
    "canberra", "hobart", "darwin",
    # ── India ─────────────────────────────────────────────────────────────────
    "india", "indian", "bharat",
    "delhi", "mumbai", "bangalore", "bengaluru", "hyderabad",
    "chennai", "kolkata", "pune", "ahmedabad", "jaipur",
    "ipc", "crpc", "hindu law", "bare act",
    "indian penal code", "indian constitution",
    "indian contract act", "bharatiya nyaya",
    # ── China ─────────────────────────────────────────────────────────────────
    "china", "chinese",
    "beijing", "shanghai", "shenzhen", "guangzhou", "hong kong", "macau",
    # ── Japan ─────────────────────────────────────────────────────────────────
    "japan", "japanese", "tokyo", "osaka", "kyoto",
    # ── Pakistan / Bangladesh ─────────────────────────────────────────────────
    "pakistan", "pakistani", "karachi", "lahore", "islamabad",
    "bangladesh", "bangladeshi", "dhaka",
    # ── Middle East ───────────────────────────────────────────────────────────
    "saudi arabia", "saudi",
    "united arab emirates", "uae",
    "dubai", "abu dhabi", "riyadh",
    "iran", "iranian", "iraq", "iraqi",
    "israel", "israeli", "turkey", "turkish",
    # ── Europe (non-UK) ───────────────────────────────────────────────────────
    "france", "french", "paris",
    "germany", "german", "berlin",
    "italy", "italian", "rome",
    "spain", "spanish", "madrid",
    "russia", "russian", "moscow",
    "ukraine", "ukrainian", "kyiv",
    "netherlands", "dutch", "amsterdam",
    "poland", "polish", "warsaw",
    "sweden", "swedish", "stockholm",
    # ── Americas (non-US) ─────────────────────────────────────────────────────
    "brazil", "brazilian", "sao paulo", "rio de janeiro",
    "mexico", "mexican", "mexico city",
    "argentina", "argentinian", "buenos aires",
    "colombia", "colombian", "bogota",
    # ── Africa ────────────────────────────────────────────────────────────────
    "nigeria", "nigerian", "lagos", "abuja",
    "south africa", "south african", "johannesburg", "cape town",
    "kenya", "kenyan", "nairobi",
    "ghana", "ghanaian", "accra",
    # ── South Korea / South-East Asia ─────────────────────────────────────────
    "south korea", "korean", "seoul",
    "indonesia", "indonesian", "jakarta",
    "philippines", "philippine", "manila",
    "vietnam", "vietnamese", "hanoi",
    "thailand", "thai", "bangkok",
    "malaysia", "malaysian", "kuala lumpur",
    "singapore",
    # ── New Zealand ───────────────────────────────────────────────────────────
    "new zealand",

    # ── UK / England (no longer allowed — NY only) ────────────────────────────
    # Previously in UK_LOCATIONS allowlist; now blocked since system is NY-only.
    "england", "scotland", "wales", "united kingdom", "great britain",
    "london", "manchester", "birmingham", "glasgow", "edinburgh",
    "liverpool", "leeds", "bristol", "sheffield", "belfast",
    "cardiff", "english law", "scots law", "welsh law",
    "uk law", "british law", "high court uk", "crown court",
}

UNSUPPORTED_RESPONSE = (
    "This assistant handles **New York legal matters only**.\n\n"
    "Your question appears to reference a jurisdiction outside New York. "
    "Legal information I generate for other jurisdictions may be inaccurate "
    "or misleading.\n\n"
    "Please consult a qualified local legal professional for advice "
    "specific to your jurisdiction."
)
```

- [ ] **Step 2: Replace `is_unsupported_jurisdiction()` function**

Find the existing `def is_unsupported_jurisdiction(question: str) -> bool:` function and replace it entirely with:

```python
def is_unsupported_jurisdiction(question: str) -> bool:
    """
    Return True if the question should be rejected on jurisdiction grounds.

    Three-step NY-first allowlist logic:
      1. Any NY signal   → False (allow — definitively New York)
      2. Any non-NY US state signal → True  (block — another US state)
      3. Any known foreign signal   → True  (block — foreign jurisdiction)
      Default: no geographic signal → False (assume New York — allow)

    The NY check runs first so "new york" is never shadowed by
    "new jersey" or "new mexico" in the other-states list.
    """
    q = question.lower()

    # Step 1 — NY signal → allow
    if any(term in q for term in NY_LOCATIONS):
        return False

    # Step 2 — non-NY US state → block
    if any(term in q for term in NON_NY_US_LOCATIONS):
        return True

    # Step 3 — foreign signal → block
    if any(term in q for term in FOREIGN_LOCATIONS):
        return True

    # Default — no geographic signal → assume NY → allow
    return False
```

- [ ] **Step 3: Update the log message in the `/questions` endpoint**

In `app.py`, find:

```python
logger.info(f"Jurisdiction blocked | session: {session_id}")
```

Replace with:

```python
logger.info(f"Non-NY jurisdiction blocked | session: {session_id}")
```

- [ ] **Step 4: Verify the backend still starts without import errors**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -c "from app import is_unsupported_jurisdiction, NY_LOCATIONS, NON_NY_US_LOCATIONS; print('OK')"
```

Expected output: `OK`

- [ ] **Step 5: Quick smoke test of the filter logic**

```bash
python -c "
from app import is_unsupported_jurisdiction
tests = [
    ('My client slipped in Manhattan', False),
    ('Car accident in NYC', False),
    ('No location mentioned injury case', False),
    ('Contract dispute in California', True),
    ('Accident in Toronto Canada', True),
    ('Filing in SDNY federal court', False),
]
for q, expected in tests:
    result = is_unsupported_jurisdiction(q)
    status = 'PASS' if result == expected else 'FAIL'
    print(f'{status}: {q!r} -> {result} (expected {expected})')
"
```

Expected: all lines print `PASS`.

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: replace US/UK jurisdiction filter with NY-first filter in backend"
```

---

## Task 3: TDD — Create ny_filter.py

**Files:**
- Create: `SaulLM-AI/ny_filter.py`
- Create: `SaulLM-AI/tests/test_ny_filter.py`
- Create: `SaulLM-AI/tests/__init__.py`

- [ ] **Step 1: Create the tests/__init__.py**

```bash
touch /home/ubuntu/saul_project/SaulLM-AI/tests/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `SaulLM-AI/tests/test_ny_filter.py`:

```python
"""
Tests for ny_filter.py — frontend New York jurisdiction filter.

check_ny_filter(question) returns (is_blocked: bool, message: str).
  is_blocked=False  → query is allowed (NY or ambiguous)
  is_blocked=True   → query is rejected (non-NY or non-legal)
  message           → empty string when not blocked, refusal message when blocked
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ny_filter import check_ny_filter


# ── Allow cases ───────────────────────────────────────────────────────────────

def test_explicit_new_york_allowed():
    blocked, msg = check_ny_filter("My client slipped in a store in New York")
    assert blocked is False
    assert msg == ""

def test_nyc_abbreviation_allowed():
    blocked, _ = check_ny_filter("Car accident happened in NYC last week")
    assert blocked is False

def test_manhattan_allowed():
    blocked, _ = check_ny_filter("Incident occurred on Broadway in Manhattan")
    assert blocked is False

def test_brooklyn_allowed():
    blocked, _ = check_ny_filter("Slip and fall at a warehouse in Brooklyn")
    assert blocked is False

def test_sdny_allowed():
    blocked, _ = check_ny_filter("Filing in SDNY federal court on diversity jurisdiction")
    assert blocked is False

def test_cplr_allowed():
    blocked, _ = check_ny_filter("What are the CPLR statute of limitations rules?")
    assert blocked is False

def test_no_location_defaults_to_ny():
    """Queries with no geographic signal default to NY — allow."""
    blocked, msg = check_ny_filter("My client was injured in a car accident")
    assert blocked is False
    assert msg == ""

def test_no_location_legal_question_allowed():
    blocked, _ = check_ny_filter("What is the standard of negligence in a slip and fall case?")
    assert blocked is False

def test_buffalo_ny_allowed():
    blocked, _ = check_ny_filter("Personal injury lawsuit filed in Buffalo")
    assert blocked is False


# ── Block: non-NY US states ───────────────────────────────────────────────────

def test_california_blocked():
    blocked, msg = check_ny_filter("My client was injured in Los Angeles, California")
    assert blocked is True
    assert "New York" in msg

def test_texas_blocked():
    blocked, msg = check_ny_filter("Contract dispute in Dallas, Texas")
    assert blocked is True
    assert "New York" in msg

def test_florida_blocked():
    blocked, msg = check_ny_filter("Slip and fall at a Miami hotel")
    assert blocked is True

def test_new_jersey_blocked():
    """new jersey must not be confused with new york."""
    blocked, msg = check_ny_filter("Car accident on the New Jersey Turnpike")
    assert blocked is True

def test_new_mexico_blocked():
    """new mexico must not be confused with new york."""
    blocked, msg = check_ny_filter("Property dispute in Albuquerque, New Mexico")
    assert blocked is True

def test_massachusetts_blocked():
    blocked, _ = check_ny_filter("Filing in Boston, Massachusetts court")
    assert blocked is True


# ── Block: foreign jurisdictions ─────────────────────────────────────────────

def test_canada_blocked():
    blocked, msg = check_ny_filter("Car accident in Toronto, Canada")
    assert blocked is True
    assert "New York" in msg

def test_india_blocked():
    blocked, _ = check_ny_filter("Contract dispute under Indian law in Mumbai")
    assert blocked is True

def test_uk_blocked():
    blocked, _ = check_ny_filter("Employment dispute under English law in London")
    assert blocked is True

def test_australia_blocked():
    blocked, _ = check_ny_filter("Personal injury claim in Sydney, Australia")
    assert blocked is True


# ── Block: non-legal queries ──────────────────────────────────────────────────

def test_car_repair_blocked():
    blocked, msg = check_ny_filter("How do I fix my car engine?")
    assert blocked is True
    assert "legal" in msg.lower()

def test_cooking_blocked():
    blocked, msg = check_ny_filter("What is the best recipe for pasta carbonara?")
    assert blocked is True

def test_it_help_blocked():
    blocked, msg = check_ny_filter("My laptop won't connect to WiFi")
    assert blocked is True


# ── NY takes precedence over non-NY ──────────────────────────────────────────

def test_ny_beats_new_jersey_in_same_query():
    """If both NY and NJ appear, NY signal should win → allow."""
    blocked, _ = check_ny_filter(
        "Case involves events in both New York and New Jersey"
    )
    assert blocked is False

def test_ny_beats_california_in_same_query():
    """Multi-jurisdiction query with explicit NY → allow."""
    blocked, _ = check_ny_filter(
        "Federal case filed in New York involving a California defendant"
    )
    assert blocked is False
```

- [ ] **Step 3: Run the tests — verify they all FAIL with ImportError**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -m pytest tests/test_ny_filter.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'ny_filter'`

- [ ] **Step 4: Create ny_filter.py**

Create `SaulLM-AI/ny_filter.py`:

```python
"""
ny_filter.py — Frontend New York Jurisdiction Filter
------------------------------------------------------
Fast keyword-based filter applied by the Streamlit frontend BEFORE
making any API call to the backend.

Two-layer enforcement strategy:
  1. This module (frontend) — instant rejection with no network latency.
  2. app.py (backend)       — server-side enforcement, source of truth.

Both layers use the same 3-step allowlist logic:
  Step 1: NY signal found        → ALLOW (assume New York)
  Step 2: Non-NY US state found  → BLOCK
  Step 3: Foreign signal found   → BLOCK
  Default: no geographic signal  → ALLOW (assume New York)

Usage:
    from ny_filter import check_ny_filter

    blocked, message = check_ny_filter("Car accident in Manhattan")
    # → (False, "")                 — allowed, no message

    blocked, message = check_ny_filter("Contract dispute in Texas")
    # → (True, "This assistant handles New York legal matters only...")
"""

# ── NY allowlist ──────────────────────────────────────────────────────────────
NY_LOCATIONS = {
    "new york", "new york city", "nyc",
    "manhattan", "brooklyn", "queens", "bronx", "staten island",
    "long island", "buffalo", "albany", "yonkers", "syracuse",
    "rochester", "white plains", "new rochelle", "mount vernon",
    "schenectady", "utica", "binghamton", "niagara falls",
    "westchester", "nassau", "suffolk",
    "new york supreme court", "new york court of appeals",
    "southern district of new york", "eastern district of new york",
    "northern district of new york", "western district of new york",
    "sdny", "edny", "ndny", "wdny",
    "new york law", "ny law", "new york state",
    "cplr", "new york penal law", "new york family court",
    "ny penal", "ny civil practice",
}

# ── Non-NY US signals ─────────────────────────────────────────────────────────
NON_NY_US_LOCATIONS = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "west virginia", "wisconsin", "wyoming",
    "district of columbia", "washington d.c.", "washington dc",
    "puerto rico", "guam", "u.s. virgin islands",
    "los angeles", "chicago", "houston", "phoenix", "philadelphia",
    "san antonio", "san diego", "dallas", "san jose", "austin",
    "jacksonville", "fort worth", "charlotte", "indianapolis",
    "san francisco", "seattle", "denver", "nashville", "oklahoma city",
    "el paso", "boston", "portland", "las vegas", "memphis", "louisville",
    "baltimore", "milwaukee", "albuquerque", "tucson", "fresno", "mesa",
    "sacramento", "atlanta", "omaha", "colorado springs", "raleigh",
    "miami", "virginia beach", "minneapolis", "tampa", "new orleans",
    "honolulu", "anaheim", "lexington", "henderson",
}

# ── Foreign signals ───────────────────────────────────────────────────────────
FOREIGN_LOCATIONS = {
    "canada", "canadian", "ontario", "quebec", "british columbia", "alberta",
    "toronto", "vancouver", "montreal", "calgary", "edmonton",
    "australia", "australian", "new south wales", "queensland",
    "sydney", "melbourne", "brisbane", "perth", "adelaide",
    "india", "indian", "bharat", "delhi", "mumbai", "bangalore",
    "bengaluru", "hyderabad", "chennai", "kolkata",
    "ipc", "crpc", "hindu law", "indian penal code",
    "china", "chinese", "beijing", "shanghai", "hong kong",
    "japan", "japanese", "tokyo", "osaka",
    "pakistan", "pakistani", "karachi", "lahore",
    "bangladesh", "bangladeshi", "dhaka",
    "saudi arabia", "uae", "dubai", "iran", "iraq", "israel", "turkey",
    "france", "french", "paris", "germany", "german", "berlin",
    "italy", "italian", "rome", "spain", "spanish", "madrid",
    "russia", "russian", "moscow", "ukraine", "ukrainian",
    "england", "scotland", "wales", "united kingdom", "great britain",
    "london", "manchester", "birmingham", "english law", "scots law",
    "uk law", "british law",
    "brazil", "mexican", "mexico city", "argentina", "colombia",
    "nigeria", "south africa", "kenya", "ghana",
    "south korea", "seoul", "indonesia", "jakarta",
    "philippines", "manila", "vietnam", "thailand", "bangkok",
    "malaysia", "singapore", "new zealand",
}

# ── Legal intent signals ──────────────────────────────────────────────────────
# Must contain at least one of these for a query to be considered legal.
LEGAL_INTENT_SIGNALS = [
    "injur", "accident", "collision", "crash", "struck by", "hit by",
    "slip and fall", "slipped", "fell at", "dog bit", "malpractice",
    "sue", "lawsuit", "legal claim", "legal action", "damages", "compensation",
    "settlement", "liable", "liability", "negligenc", "plaintiff", "defendant",
    "attorney", "lawyer", "court", "filing", "complaint", "civil action",
    "arrest", "charg", "crime", "criminal", "accused", "prosecut",
    "indicted", "dui", "dwi", "bail", "plea",
    "terminat", "wrongful", "discriminat", "harass", "retaliat",
    "unpaid wage", "wage theft", "fired unfair", "hostile work",
    "breach of contract", "contract dispute", "breach", "contract violat",
    "did not deliver", "not paid per", "broke our agreement",
    "vandali", "stolen", "theft", "broke into", "burglar", "trespass",
    "property damage", "my property was",
    "divorce", "custody", "child support", "adoption", "alimony",
    "separation agreement", "marital",
    "eminent domain", "government took", "condemned my", "seized my",
    "city took", "state took",
    "statute", "jurisdiction", "venue", "motion", "appeal", "deposition",
    "subpoena", "discovery", "evidence", "witness",
]

# ── Response messages ─────────────────────────────────────────────────────────
_NON_NY_RESPONSE = (
    "This assistant handles **New York legal matters only**.\n\n"
    "Your question appears to reference a jurisdiction outside New York. "
    "Legal information generated for other jurisdictions may be inaccurate "
    "or misleading.\n\n"
    "Please consult a qualified local legal professional for advice "
    "specific to your jurisdiction."
)

_NON_LEGAL_RESPONSE = (
    "This assistant handles **New York legal cases only**. "
    "Your question does not appear to describe a legal situation such as "
    "an injury, contract dispute, property damage, criminal charge, or employment issue.\n\n"
    "If you have a New York legal matter, please describe it — for example:\n"
    "- *'My client was injured in a car accident in Manhattan'*\n"
    "- *'I was wrongfully terminated by my employer in Brooklyn'*\n"
    "- *'Someone vandalized my property in Queens'*\n\n"
    "I'm here to help with New York legal questions and complaint drafting."
)


def check_ny_filter(question: str) -> tuple[bool, str]:
    """
    Check whether a query should be blocked by the NY filter.

    Returns:
        (is_blocked, message)
        is_blocked=False, message=""     → query is allowed
        is_blocked=True,  message=<str>  → query is blocked, message is the refusal

    Logic (evaluated in order):
        1. NY signal found          → allow (False, "")
        2. No legal intent detected → block with non-legal message
        3. Non-NY US state found    → block with non-NY message
        4. Foreign signal found     → block with non-NY message
        5. Default (no location)    → allow (False, "")
    """
    q = question.lower().strip()

    # Step 1 — explicit NY signal → allow immediately
    if any(term in q for term in NY_LOCATIONS):
        return False, ""

    # Step 2 — no legal intent → block before checking geography
    if not any(signal in q for signal in LEGAL_INTENT_SIGNALS):
        return True, _NON_LEGAL_RESPONSE

    # Step 3 — non-NY US state → block
    if any(term in q for term in NON_NY_US_LOCATIONS):
        return True, _NON_NY_RESPONSE

    # Step 4 — foreign signal → block
    if any(term in q for term in FOREIGN_LOCATIONS):
        return True, _NON_NY_RESPONSE

    # Default — no geographic signal → assume NY → allow
    return False, ""
```

- [ ] **Step 5: Run the tests — verify they all PASS**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -m pytest tests/test_ny_filter.py -v
```

Expected: all tests pass. Output ends with `X passed`.

- [ ] **Step 6: Commit**

```bash
git add ny_filter.py tests/__init__.py tests/test_ny_filter.py
git commit -m "feat: add ny_filter.py with NY-first jurisdiction logic and tests"
```

---

## Task 4: TDD — Create backend_client.py

**Files:**
- Create: `SaulLM-AI/backend_client.py`
- Create: `SaulLM-AI/tests/test_backend_client.py`

The backend runs at `http://localhost:9000` by default (configurable via `BACKEND_URL` env var). The `API_KEY` env var is forwarded as a Bearer token if set.

- [ ] **Step 1: Write the failing tests**

Create `SaulLM-AI/tests/test_backend_client.py`:

```python
"""
Tests for backend_client.py — all HTTP calls to the FastAPI backend.

Uses unittest.mock to patch requests.post/patch so no real backend is needed.
"""
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backend_client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_json_response(payload: dict, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.status_code = status_code
    mock_resp.raise_for_status.return_value = None
    return mock_resp

def _mock_bytes_response(content: bytes, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.content = content
    mock_resp.status_code = status_code
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ── ask_question ──────────────────────────────────────────────────────────────

MOCK_QUESTION_RESP = {
    "answer": "Legal analysis here.",
    "session_id": "sess-abc-123",
    "offer_complaint": True,
    "case_type": "personal_injury",
    "required_elements": [{"id": "plaintiff_name", "label": "Plaintiff Name"}],
    "sections": {},
    "classification_low_confidence": False,
}

def test_ask_question_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("My client was injured in NYC")
        url = mock_post.call_args[0][0]
        assert url.endswith("/questions")

def test_ask_question_sends_question_in_body():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("My client was injured in NYC")
        body = mock_post.call_args[1]["json"]
        assert body["question"] == "My client was injured in NYC"

def test_ask_question_sends_session_id_when_provided():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("follow up", session_id="existing-sess")
        body = mock_post.call_args[1]["json"]
        assert body["session_id"] == "existing-sess"

def test_ask_question_omits_session_id_when_none():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        backend_client.ask_question("first question", session_id=None)
        body = mock_post.call_args[1]["json"]
        assert "session_id" not in body

def test_ask_question_returns_parsed_dict():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_QUESTION_RESP)
        result = backend_client.ask_question("My client was injured in NYC")
        assert result["session_id"] == "sess-abc-123"
        assert result["offer_complaint"] is True
        assert result["case_type"] == "personal_injury"

def test_ask_question_raises_on_connection_error():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        with pytest.raises(requests.exceptions.ConnectionError):
            backend_client.ask_question("test")

def test_ask_question_raises_on_http_error():
    with patch("backend_client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("503")
        mock_post.return_value = mock_resp
        with pytest.raises(requests.exceptions.HTTPError):
            backend_client.ask_question("test")


# ── start_intake ──────────────────────────────────────────────────────────────

MOCK_INTAKE_START_RESP = {
    "case_id": "case-xyz-456",
    "case_type": "personal_injury",
    "is_complete": False,
    "pre_filled": {"plaintiff_name": "John Doe"},
    "missing_required": [{"id": "defendant_name", "label": "Defendant Name", "description": "...", "section": "Parties"}],
    "missing_questions": ["Who is the defendant?"],
    "missing_optional": [],
    "sections_display": {},
    "validation": {"is_valid": False, "can_draft": False, "issues": [], "sol_warning": None},
}

def test_start_intake_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.start_intake("sess-1", "personal_injury", "John Doe was injured")
        url = mock_post.call_args[0][0]
        assert url.endswith("/intake/start")

def test_start_intake_sends_correct_body():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.start_intake("sess-1", "personal_injury", "John Doe was injured")
        body = mock_post.call_args[1]["json"]
        assert body["session_id"] == "sess-1"
        assert body["case_type"] == "personal_injury"
        assert body["initial_text"] == "John Doe was injured"

def test_start_intake_returns_case_id():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        result = backend_client.start_intake("sess-1", "personal_injury", "text")
        assert result["case_id"] == "case-xyz-456"


# ── provide_intake ────────────────────────────────────────────────────────────

def test_provide_intake_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.provide_intake("case-xyz-456", "The defendant is Acme Corp")
        url = mock_post.call_args[0][0]
        assert "case-xyz-456/provide" in url

def test_provide_intake_sends_text_in_body():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_INTAKE_START_RESP)
        backend_client.provide_intake("case-xyz-456", "The defendant is Acme Corp")
        body = mock_post.call_args[1]["json"]
        assert body["text"] == "The defendant is Acme Corp"


# ── force_draft ───────────────────────────────────────────────────────────────

def test_force_draft_uses_patch_method():
    with patch("backend_client.requests.patch") as mock_patch:
        mock_patch.return_value = _mock_json_response({"case_id": "case-xyz", "force_draft": True})
        backend_client.force_draft("case-xyz-456")
        url = mock_patch.call_args[0][0]
        assert "case-xyz-456/force" in url


# ── generate_draft ────────────────────────────────────────────────────────────

MOCK_DRAFT_RESP = {
    "case_id": "case-xyz-456",
    "case_type": "personal_injury",
    "complaint": "IN THE UNITED STATES DISTRICT COURT...",
    "from_cache": False,
    "word_count": 900,
    "unknown_count": 0,
}

def test_generate_draft_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_DRAFT_RESP)
        backend_client.generate_draft("case-xyz-456")
        url = mock_post.call_args[0][0]
        assert url.endswith("/draft/case-xyz-456")

def test_generate_draft_returns_complaint_text():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_json_response(MOCK_DRAFT_RESP)
        result = backend_client.generate_draft("case-xyz-456")
        assert result["complaint"] == "IN THE UNITED STATES DISTRICT COURT..."
        assert result["word_count"] == 900


# ── download_docx ─────────────────────────────────────────────────────────────

def test_download_docx_posts_to_correct_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"PK\x03\x04fakeDOCX")
        backend_client.download_docx("case-xyz-456")
        url = mock_post.call_args[0][0]
        assert url.endswith("/document/case-xyz-456")
        assert "pdf" not in url

def test_download_docx_returns_bytes():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"PK\x03\x04fakeDOCX")
        result = backend_client.download_docx("case-xyz-456")
        assert isinstance(result, bytes)


# ── download_pdf ──────────────────────────────────────────────────────────────

def test_download_pdf_posts_to_pdf_url():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"%PDF-1.4 fakePDF")
        backend_client.download_pdf("case-xyz-456")
        url = mock_post.call_args[0][0]
        assert url.endswith("/document/case-xyz-456/pdf")

def test_download_pdf_returns_bytes():
    with patch("backend_client.requests.post") as mock_post:
        mock_post.return_value = _mock_bytes_response(b"%PDF-1.4 fakePDF")
        result = backend_client.download_pdf("case-xyz-456")
        assert isinstance(result, bytes)
```

- [ ] **Step 2: Run the tests — verify they FAIL with ImportError**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -m pytest tests/test_backend_client.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'backend_client'`

- [ ] **Step 3: Create backend_client.py**

Create `SaulLM-AI/backend_client.py`:

```python
"""
backend_client.py — FastAPI Backend HTTP Client
-------------------------------------------------
Single place for all HTTP calls to the SaulLM-AI FastAPI backend.
The Streamlit app imports functions from here — it never calls requests directly.

Configuration (via environment variables):
    BACKEND_URL : Base URL of the FastAPI server (default: http://localhost:9000)
    API_KEY     : Bearer token for /intake/* and /draft/* endpoints (default: empty)

All functions raise requests.exceptions.HTTPError on non-2xx responses.
ConnectionError is propagated as-is so the caller can show a "backend unreachable" message.
"""

import os
import requests

BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:9000").rstrip("/")
_API_KEY:    str = os.getenv("API_KEY", "")


def _headers() -> dict:
    """Build request headers, including Bearer token if API_KEY is configured."""
    h = {"Content-Type": "application/json"}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


# ──────────────────────────────────────────────
# Chat Q&A
# ──────────────────────────────────────────────

def ask_question(question: str, session_id: str | None = None) -> dict:
    """
    POST /questions — ask a legal question, get an IRAC analysis back.

    Args:
        question   : The attorney's question or case description.
        session_id : Existing chat session UUID, or None to start a new session.

    Returns:
        {
            "answer":            str,
            "session_id":        str,
            "offer_complaint":   bool,
            "case_type":         str,
            "required_elements": list[dict],
            "sections":          dict,
            "classification_low_confidence": bool
        }

    Raises:
        requests.exceptions.ConnectionError — backend is unreachable
        requests.exceptions.HTTPError       — non-2xx response (e.g. 503 model loading)
    """
    payload: dict = {"question": question}
    if session_id:
        payload["session_id"] = session_id

    resp = requests.post(
        f"{BACKEND_URL}/questions",
        json=payload,
        headers=_headers(),
        timeout=120,   # SaulLM inference can take up to ~60s
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# Intake loop
# ──────────────────────────────────────────────

def start_intake(session_id: str, case_type: str, initial_text: str) -> dict:
    """
    POST /intake/start — start a new case intake session.

    Args:
        session_id   : Chat session UUID (from ask_question response).
        case_type    : Case type string (e.g. "personal_injury").
        initial_text : The attorney's original question/description used to auto-fill fields.

    Returns:
        {
            "case_id":          str,
            "case_type":        str,
            "is_complete":      bool,
            "pre_filled":       dict,          # fields auto-extracted from initial_text
            "missing_required": list[dict],    # [{id, label, description, section}, ...]
            "missing_questions": list[str],    # human-readable prompts for missing fields
            "missing_optional": list[dict],
            "sections_display": dict,
            "validation":       dict
        }

    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/intake/start",
        json={
            "session_id":   session_id,
            "case_type":    case_type,
            "initial_text": initial_text,
        },
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def provide_intake(case_id: str, text: str) -> dict:
    """
    POST /intake/{case_id}/provide — send follow-up information for field extraction.

    Args:
        case_id : Case session UUID (from start_intake response).
        text    : Free-form prose. Entity extraction runs on this and merges into fields.

    Returns: Same shape as start_intake response.
    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/intake/{case_id}/provide",
        json={"text": text},
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def force_draft(case_id: str) -> dict:
    """
    PATCH /intake/{case_id}/force — acknowledge missing fields and proceed to draft.

    Sets force_draft=1 on the case session so the complaint engine will generate
    with [UNKNOWN] placeholders for any missing required fields.

    Returns:
        {
            "case_id":          str,
            "force_draft":      True,
            "missing_required": list[dict]    # fields that will be [UNKNOWN]
        }

    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.patch(
        f"{BACKEND_URL}/intake/{case_id}/force",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# Complaint drafting
# ──────────────────────────────────────────────

def generate_draft(case_id: str) -> dict:
    """
    POST /draft/{case_id} — generate a formal complaint via Claude API.

    If a draft already exists for this case_id, the cached version is returned
    immediately (no duplicate Claude API charge).

    Returns:
        {
            "case_id":       str,
            "case_type":     str,
            "complaint":     str,    # full complaint text
            "from_cache":    bool,
            "word_count":    int,
            "unknown_count": int     # [UNKNOWN] placeholders remaining
        }

    Raises:
        requests.exceptions.ConnectionError — backend unreachable
        requests.exceptions.HTTPError 503   — Claude API unavailable / timed out
        requests.exceptions.HTTPError 500   — unexpected server error
    """
    resp = requests.post(
        f"{BACKEND_URL}/draft/{case_id}",
        headers=_headers(),
        timeout=90,   # Claude Sonnet typically responds in 10–30s; 90s is safe margin
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# Document export
# ──────────────────────────────────────────────

def download_docx(case_id: str) -> bytes:
    """
    POST /document/{case_id} — generate and return DOCX bytes.

    Returns: Raw DOCX bytes (application/vnd.openxmlformats-officedocument...)
    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/document/{case_id}",
        json={},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def download_pdf(case_id: str) -> bytes:
    """
    POST /document/{case_id}/pdf — generate and return PDF bytes.

    Returns: Raw PDF bytes (application/pdf)
    Raises: requests.exceptions.ConnectionError, requests.exceptions.HTTPError
    """
    resp = requests.post(
        f"{BACKEND_URL}/document/{case_id}/pdf",
        json={},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content
```

- [ ] **Step 4: Run the tests — verify they all PASS**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -m pytest tests/test_backend_client.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend_client.py tests/test_backend_client.py
git commit -m "feat: add backend_client.py with all API wrappers and tests"
```

---

## Task 5: Create styles.py

**Files:**
- Create: `SaulLM-AI/styles.py`

No tests needed — this is a pure CSS string. Visual correctness is verified in Task 7.

- [ ] **Step 1: Create styles.py**

Create `SaulLM-AI/styles.py`:

```python
"""
styles.py — Nyaay AI CSS Theme
--------------------------------
Inject via: st.markdown(get_css(), unsafe_allow_html=True)

Color palette:
    --bg-dark    #0A1628   page background
    --bg-card    #0F1E3D   message bubbles, cards
    --blue-mid   #1E3FCC   gradient midpoint
    --cyan       #38BDF8   accent, active buttons, highlights
    --text       #FFFFFF   primary text
    --muted      #94A3B8   secondary text
    --border     rgba(56,189,248,0.2)
"""


def get_css() -> str:
    return """
<style>
/* ── Hide Streamlit chrome ─────────────────────────────────────────────── */
#MainMenu  { visibility: hidden; }
footer     { visibility: hidden; }
header     { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }

/* ── Full-page dark gradient background ────────────────────────────────── */
.stApp {
    background: linear-gradient(135deg, #0A1628 0%, #0F1E3D 60%, #152040 100%);
    min-height: 100vh;
}

.main .block-container {
    background: transparent;
    padding-top: 1.5rem;
    padding-bottom: 5rem;
    max-width: 800px;
}

/* ── Typography ─────────────────────────────────────────────────────────── */
h1, h2, h3, .stMarkdown p, label, span, div {
    color: #FFFFFF !important;
}

.stCaption, .stMarkdown small {
    color: #94A3B8 !important;
}

/* ── Title gradient text ────────────────────────────────────────────────── */
h1 {
    background: linear-gradient(90deg, #FFFFFF 40%, #38BDF8 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-size: 1.8rem !important;
    font-weight: 700 !important;
    margin-bottom: 0 !important;
}

/* ── Chat messages ──────────────────────────────────────────────────────── */
[data-testid="stChatMessage"] {
    background: #0F1E3D;
    border: 1px solid rgba(56, 189, 248, 0.15);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    margin: 0.4rem 0;
}

/* User message — subtle cyan tint */
[data-testid="stChatMessage"][data-testid*="user"] {
    background: rgba(56, 189, 248, 0.07);
    border-color: rgba(56, 189, 248, 0.25);
}

/* ── Chat input box ─────────────────────────────────────────────────────── */
[data-testid="stChatInput"] textarea,
[data-testid="stChatInputTextArea"] {
    background: #0F1E3D !important;
    border: 1px solid rgba(56, 189, 248, 0.3) !important;
    border-radius: 12px !important;
    color: #FFFFFF !important;
    caret-color: #38BDF8 !important;
    font-size: 0.95rem !important;
}

[data-testid="stChatInput"] textarea:focus,
[data-testid="stChatInputTextArea"]:focus {
    border-color: #38BDF8 !important;
    box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.15) !important;
    outline: none !important;
}

/* ── Buttons ────────────────────────────────────────────────────────────── */
/* Primary — Generate Complaint */
.stButton > button[kind="primary"],
.stButton > button[data-testid*="primary"] {
    background: linear-gradient(135deg, #38BDF8 0%, #1E3FCC 100%) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    padding: 0.5rem 1.5rem !important;
    transition: opacity 0.15s ease !important;
}

.stButton > button[kind="primary"]:hover {
    opacity: 0.88 !important;
}

/* Disabled — greyed out */
.stButton > button:disabled {
    background: #1A2540 !important;
    color: #4A5568 !important;
    border: 1px solid #2A3550 !important;
    cursor: not-allowed !important;
}

/* Secondary / default buttons */
.stButton > button:not([kind="primary"]):not(:disabled) {
    background: rgba(56, 189, 248, 0.08) !important;
    border: 1px solid rgba(56, 189, 248, 0.3) !important;
    color: #FFFFFF !important;
    border-radius: 8px !important;
}

/* Download buttons */
.stDownloadButton > button {
    background: rgba(56, 189, 248, 0.1) !important;
    border: 1px solid #38BDF8 !important;
    color: #38BDF8 !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
}

.stDownloadButton > button:hover {
    background: rgba(56, 189, 248, 0.2) !important;
}

/* ── Alert / warning boxes ──────────────────────────────────────────────── */
[data-testid="stAlert"] {
    background: rgba(15, 30, 61, 0.95) !important;
    border-left: 3px solid #38BDF8 !important;
    border-radius: 8px !important;
    color: #FFFFFF !important;
}

[data-testid="stAlert"] p {
    color: #FFFFFF !important;
}

/* ── Text area (draft display) ──────────────────────────────────────────── */
.stTextArea textarea {
    background: #060E1E !important;
    border: 1px solid rgba(56, 189, 248, 0.25) !important;
    border-radius: 8px !important;
    color: #E2E8F0 !important;
    font-family: 'Courier New', Courier, monospace !important;
    font-size: 0.85rem !important;
    line-height: 1.6 !important;
}

/* ── Divider ────────────────────────────────────────────────────────────── */
hr {
    border-color: rgba(56, 189, 248, 0.15) !important;
    margin: 1.5rem 0 !important;
}

/* ── Subheader ──────────────────────────────────────────────────────────── */
h2, h3 {
    color: #FFFFFF !important;
    font-weight: 600 !important;
}

/* ── Spinner ────────────────────────────────────────────────────────────── */
[data-testid="stSpinner"] {
    color: #38BDF8 !important;
}

/* ── Scrollbar ──────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0A1628; }
::-webkit-scrollbar-thumb { background: rgba(56, 189, 248, 0.3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(56, 189, 248, 0.5); }
</style>
"""
```

- [ ] **Step 2: Verify the CSS string is importable**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -c "from styles import get_css; css = get_css(); assert '<style>' in css; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add styles.py
git commit -m "feat: add Nyaay AI CSS theme in styles.py"
```

---

## Task 6: Create streamlit_app.py

**Files:**
- Create: `SaulLM-AI/streamlit_app.py`

This is the main UI file. It imports from `ny_filter`, `backend_client`, and `styles` — never calls `requests` directly.

- [ ] **Step 1: Create streamlit_app.py**

Create `SaulLM-AI/streamlit_app.py`:

```python
"""
streamlit_app.py — Nyaay AI Chat Interface
-------------------------------------------
ChatGPT/Claude-style legal assistant for New York attorneys.

Flow:
  1. Attorney types a legal question or case description.
  2. NY filter check — non-NY and non-legal queries refused immediately.
  3. POST /questions → legal IRAC analysis displayed in chat.
  4. If backend returns offer_complaint=True, silently start intake session.
  5. Subsequent messages also sent to /intake/provide for field extraction.
  6. Generate Complaint button activates when mandatory fields are detected.
  7. If mandatory fields are missing: force-warning panel → Proceed Anyway.
  8. Draft displayed in-page with Download DOCX and Download PDF buttons.

Run:
    streamlit run streamlit_app.py

Environment:
    BACKEND_URL  : FastAPI backend base URL (default: http://localhost:9000)
    API_KEY      : Bearer token for protected endpoints (default: empty)
"""

import streamlit as st
import requests as req_lib

from ny_filter      import check_ny_filter
from backend_client import (
    ask_question,
    start_intake,
    provide_intake,
    force_draft,
    generate_draft,
    download_docx,
    download_pdf,
)
from styles import get_css

# ──────────────────────────────────────────────
# Page config — must be the first Streamlit call
# ──────────────────────────────────────────────
st.set_page_config(
    page_title = "Nyaay AI — New York Legal Assistant",
    page_icon  = "⚖️",
    layout     = "centered",
)
st.markdown(get_css(), unsafe_allow_html=True)


# ──────────────────────────────────────────────
# Session state
# ──────────────────────────────────────────────
# Flow states:
#   CHAT_ONLY       — no case detected yet, just Q&A
#   INTAKE_ACTIVE   — case detected, collecting fields silently
#   READY_TO_DRAFT  — all mandatory fields filled
#   DRAFTING        — spinner shown while Claude generates
#   DRAFT_COMPLETE  — complaint displayed with download buttons

_DEFAULTS: dict = {
    "messages":          [],       # [{role: "user"|"assistant", content: str}]
    "session_id":        None,     # backend chat session UUID
    "case_id":           None,     # intake case UUID
    "case_type":         None,     # e.g. "personal_injury"
    "required_elements": [],       # list of element dicts from /questions
    "provided_fields":   {},       # fields collected so far
    "missing_fields":    [],       # field IDs still missing (required only)
    "flow_state":        "CHAT_ONLY",
    "draft_text":        None,
    "show_force_warning":False,
    "error_message":     None,
}


def _init_state() -> None:
    for key, val in _DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ──────────────────────────────────────────────
# Intake helpers (silent — no UI side effects)
# ──────────────────────────────────────────────

def _start_intake_silently(session_id: str, case_type: str, initial_text: str) -> None:
    """
    Start the backend intake session after case detection.
    Failures are silently swallowed — chat still works if intake fails.
    """
    try:
        resp = start_intake(session_id, case_type, initial_text)
        st.session_state.case_id       = resp["case_id"]
        st.session_state.provided_fields = dict(resp.get("pre_filled", {}))
        missing_req = resp.get("missing_required", [])
        st.session_state.missing_fields = [f["id"] for f in missing_req]
        st.session_state.flow_state = (
            "READY_TO_DRAFT" if not missing_req else "INTAKE_ACTIVE"
        )
    except Exception:
        # Intake failure is non-fatal — attorney can still chat
        pass


def _update_intake(case_id: str, text: str) -> None:
    """
    Send follow-up text to the intake endpoint and update local field state.
    Silent — does not display anything.
    """
    try:
        resp = provide_intake(case_id, text)
        # Merge newly extracted fields into local state
        newly_filled = resp.get("pre_filled", {})
        st.session_state.provided_fields.update(newly_filled)
        missing_req = resp.get("missing_required", [])
        st.session_state.missing_fields = [f["id"] for f in missing_req]
        if not missing_req:
            st.session_state.flow_state = "READY_TO_DRAFT"
    except Exception:
        pass


# ──────────────────────────────────────────────
# Draft generation helper
# ──────────────────────────────────────────────

def _do_generate() -> None:
    """
    Call generate_draft on the backend. Updates flow_state to DRAFT_COMPLETE
    on success, or back to READY_TO_DRAFT with an error message on failure.
    """
    st.session_state.flow_state = "DRAFTING"
    try:
        result = generate_draft(st.session_state.case_id)
        st.session_state.draft_text  = result["complaint"]
        st.session_state.flow_state  = "DRAFT_COMPLETE"
    except req_lib.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code == 503:
            st.session_state.error_message = (
                "Draft generation timed out. Click Generate to retry."
            )
        else:
            st.session_state.error_message = (
                f"Draft generation failed (HTTP {code}). Please try again."
            )
        st.session_state.flow_state = "READY_TO_DRAFT"
    except req_lib.exceptions.ConnectionError:
        st.session_state.error_message = (
            "Unable to connect to the legal AI service. Please try again."
        )
        st.session_state.flow_state = "READY_TO_DRAFT"
    except Exception as e:
        st.session_state.error_message = f"An unexpected error occurred: {e}"
        st.session_state.flow_state = "READY_TO_DRAFT"


# ──────────────────────────────────────────────
# Message handler
# ──────────────────────────────────────────────

def handle_user_message(user_input: str) -> None:
    """
    Full pipeline for a single attorney message:
      1. Add to chat history
      2. NY filter (frontend)
      3. POST /questions (backend)
      4. Auto-detect case → start intake silently if offer_complaint=True
      5. If intake active → call provide_intake with each message
    """
    # Add user message to display history immediately
    st.session_state.messages.append({"role": "user", "content": user_input})

    # ── Frontend NY filter ────────────────────────────────────────────────────
    blocked, refusal_msg = check_ny_filter(user_input)
    if blocked:
        st.session_state.messages.append({"role": "assistant", "content": refusal_msg})
        return

    # ── Call backend /questions ───────────────────────────────────────────────
    try:
        resp = ask_question(user_input, st.session_state.session_id)
    except req_lib.exceptions.ConnectionError:
        st.session_state.messages.append({
            "role":    "assistant",
            "content": "Unable to connect to the legal AI service. Please try again.",
        })
        return
    except req_lib.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code == 503:
            msg = "The AI model is still loading. Please wait a moment and try again."
        else:
            msg = f"An error occurred (HTTP {code}). Please try again."
        st.session_state.messages.append({"role": "assistant", "content": msg})
        return
    except Exception:
        st.session_state.messages.append({
            "role":    "assistant",
            "content": "Unable to connect to the legal AI service. Please try again.",
        })
        return

    # ── Update session ID ─────────────────────────────────────────────────────
    st.session_state.session_id = resp.get("session_id", st.session_state.session_id)

    # ── Add assistant answer to chat ──────────────────────────────────────────
    st.session_state.messages.append({"role": "assistant", "content": resp["answer"]})

    # ── Auto case detection ───────────────────────────────────────────────────
    # offer_complaint=True means the backend detected a legal case and classified it.
    # We silently start the intake loop so the Generate button can activate automatically.
    if resp.get("offer_complaint") and st.session_state.flow_state == "CHAT_ONLY":
        st.session_state.case_type         = resp.get("case_type")
        st.session_state.required_elements = resp.get("required_elements", [])
        _start_intake_silently(
            session_id   = st.session_state.session_id,
            case_type    = resp["case_type"],
            initial_text = user_input,
        )
    elif st.session_state.flow_state in ("INTAKE_ACTIVE", "READY_TO_DRAFT") \
            and st.session_state.case_id:
        # Feed every subsequent message into intake for continuous field extraction
        _update_intake(st.session_state.case_id, user_input)


# ──────────────────────────────────────────────
# UI Renderers
# ──────────────────────────────────────────────

def _render_header() -> None:
    st.title("⚖️ Nyaay AI")
    st.caption("New York Legal Assistant — Powered by AI")


def _render_chat() -> None:
    """Render all chat messages in order."""
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _render_generate_area() -> None:
    """
    Render the Generate Complaint button and, when active,
    the force-warning panel if mandatory fields are missing.
    """
    flow = st.session_state.flow_state

    # Don't show generate button in terminal states or during drafting
    if flow in ("DRAFTING", "DRAFT_COMPLETE"):
        if flow == "DRAFTING":
            st.info("Generating complaint… this may take up to 60 seconds.")
        return

    st.divider()

    # CHAT_ONLY — button shown but disabled with helper text
    if flow == "CHAT_ONLY":
        col1, col2 = st.columns([3, 1])
        with col2:
            st.button("Generate Complaint", disabled=True, key="gen_disabled")
        with col1:
            st.caption("Describe your New York case to unlock complaint generation.")
        return

    # INTAKE_ACTIVE or READY_TO_DRAFT — button is active
    has_missing = bool(st.session_state.missing_fields)

    # ── Force warning panel ───────────────────────────────────────────────────
    if st.session_state.show_force_warning:
        st.warning(
            "The following required fields are missing and will appear as "
            "**[UNKNOWN]** in the generated complaint:"
        )
        for fid in st.session_state.missing_fields:
            st.markdown(f"&nbsp;&nbsp;• {fid.replace('_', ' ').title()}")
        st.markdown("Do you want to proceed anyway?")

        col1, col2, _ = st.columns([1, 1, 2])
        with col1:
            if st.button("Go Back", key="go_back"):
                st.session_state.show_force_warning = False
                st.rerun()
        with col2:
            if st.button("Proceed Anyway", type="primary", key="proceed_anyway"):
                st.session_state.show_force_warning = False
                try:
                    force_draft(st.session_state.case_id)
                except Exception:
                    pass   # force endpoint failure is non-fatal — draft will still use [UNKNOWN]
                _do_generate()
                st.rerun()
        return

    # ── Normal generate button ────────────────────────────────────────────────
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("Generate Complaint", type="primary", key="gen_active"):
            if has_missing:
                st.session_state.show_force_warning = True
                st.rerun()
            else:
                _do_generate()
                st.rerun()
    with col1:
        if has_missing:
            n = len(st.session_state.missing_fields)
            st.caption(
                f"⚠️ {n} required field{'s' if n > 1 else ''} still missing — "
                "you can proceed anyway and fill in [UNKNOWN] fields before filing."
            )
        else:
            st.caption("✓ All required fields collected — ready to generate complaint.")


def _render_draft() -> None:
    """
    Render the complaint draft and download buttons after generation.
    Only shown when flow_state == DRAFT_COMPLETE.
    """
    if st.session_state.flow_state != "DRAFT_COMPLETE" or not st.session_state.draft_text:
        return

    st.divider()
    st.subheader("📄 Generated Complaint Draft")

    # Read-only scrollable text area
    st.text_area(
        label             = "complaint_display",
        value             = st.session_state.draft_text,
        height            = 520,
        disabled          = True,
        label_visibility  = "collapsed",
    )

    # Download buttons + new case
    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        try:
            docx_bytes = download_docx(st.session_state.case_id)
            st.download_button(
                label     = "⬇ Download DOCX",
                data      = docx_bytes,
                file_name = f"complaint_{st.session_state.case_id[:8]}.docx",
                mime      = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key       = "dl_docx",
            )
        except Exception:
            st.error("DOCX download failed. Please try again.")

    with col2:
        try:
            pdf_bytes = download_pdf(st.session_state.case_id)
            st.download_button(
                label     = "⬇ Download PDF",
                data      = pdf_bytes,
                file_name = f"complaint_{st.session_state.case_id[:8]}.pdf",
                mime      = "application/pdf",
                key       = "dl_pdf",
            )
        except Exception:
            st.error("PDF download failed. Please try again.")

    with col3:
        if st.button("Start New Case", key="new_case"):
            # Reset all session state to start fresh
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    _init_state()
    _render_header()

    # Show any error that was set during the last run
    if st.session_state.error_message:
        st.error(st.session_state.error_message)
        st.session_state.error_message = None

    _render_chat()
    _render_generate_area()
    _render_draft()

    # Chat input — hidden after draft is complete (use Start New Case instead)
    if st.session_state.flow_state not in ("DRAFTING", "DRAFT_COMPLETE"):
        if user_input := st.chat_input("Describe your New York legal case or ask a question…"):
            handle_user_message(user_input)
            st.rerun()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the file is parseable (no syntax errors)**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -c "import ast; ast.parse(open('streamlit_app.py').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: Verify all imports resolve**

```bash
python -c "
from ny_filter import check_ny_filter
from backend_client import ask_question, start_intake, provide_intake, force_draft, generate_draft, download_docx, download_pdf
from styles import get_css
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 4: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: add streamlit_app.py — Nyaay AI chat interface"
```

---

## Task 7: Run all tests and manual smoke test

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
python -m pytest tests/ -v
```

Expected: all tests pass. Count should be 24+ tests (ny_filter + backend_client).

- [ ] **Step 2: Start the Streamlit app**

Make sure the FastAPI backend is running first (in a separate terminal):

```bash
# Terminal 1 — start backend (if not already running)
cd /home/ubuntu/saul_project/SaulLM-AI
python app.py
```

Then start Streamlit:

```bash
# Terminal 2 — start frontend
cd /home/ubuntu/saul_project/SaulLM-AI
BACKEND_URL=http://localhost:9000 streamlit run streamlit_app.py --server.port 8501
```

Expected: browser opens at `http://localhost:8501`

- [ ] **Step 3: Smoke test — NY filter**

In the chat input, type:
```
How do I fix my car engine?
```
Expected: assistant responds with the non-legal refusal message (no API call made).

Then type:
```
My client was injured in a car accident in Dallas, Texas
```
Expected: assistant responds with the non-NY refusal message.

- [ ] **Step 4: Smoke test — NY legal question**

Type:
```
My client John Doe slipped and fell at a grocery store on 5th Avenue in Manhattan on March 10, 2024. The store owner, Acme Groceries LLC, failed to clean up a spill. John suffered a broken arm and incurred $15,000 in medical expenses.
```
Expected:
- Assistant returns an IRAC legal analysis
- Generate Complaint button becomes active (or shows warning count)

- [ ] **Step 5: Smoke test — Generate and download**

Click "Generate Complaint".
- If missing fields warning appears: click "Proceed Anyway"
- Expected: spinner for up to 60 seconds, then complaint draft displayed
- Expected: "Download DOCX" and "Download PDF" buttons visible
- Click Download DOCX — browser should download a `.docx` file
- Click Download PDF — browser should download a `.pdf` file

- [ ] **Step 6: Smoke test — Start New Case**

Click "Start New Case". Expected: chat resets to empty state, Generate button is disabled again.

- [ ] **Step 7: Final commit**

```bash
cd /home/ubuntu/saul_project/SaulLM-AI
git add .
git commit -m "feat: Nyaay AI Streamlit chat interface — complete MVP"
```

---

## Success Criteria Checklist

- [ ] All 24+ unit tests pass (`pytest tests/`)
- [ ] Non-NY queries (Texas, California, Toronto) are refused immediately without API call
- [ ] Non-legal queries are refused immediately without API call
- [ ] NY queries (Manhattan, Brooklyn, no-location) get through to the backend
- [ ] Generate button is disabled until a case is detected
- [ ] Generate button activates once `offer_complaint=True` from backend
- [ ] Force warning lists missing fields and "Proceed Anyway" works
- [ ] Draft appears in-page after generation
- [ ] DOCX download produces a valid `.docx` file
- [ ] PDF download produces a valid `.pdf` file
- [ ] "Start New Case" resets all state
- [ ] Nyaay AI dark theme renders correctly (dark background, cyan accents)
- [ ] Page does not crash on backend errors — shows inline error messages
