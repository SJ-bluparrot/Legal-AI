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

# ── Conversational openers (always allowed — handled by backend gracefully) ───
GREETING_PATTERNS = [
    "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
    "good night", "howdy", "greetings", "what can you do", "what do you do",
    "how does this work", "help", "who are you", "what are you",
    "can you help", "help me", "i need help", "i need assistance",
    "how do i", "how do you", "what do you help", "can you assist",
    "get started", "where do i start", "how to start", "how to use",
]

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

    # Step 0 — greeting / conversational opener → allow immediately
    if any(q == g or q.startswith(g) for g in GREETING_PATTERNS):
        return False, ""

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
