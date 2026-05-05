"""
app.py — Legal AI Assistant Backend
-------------------------------------
All AI inference handled via Anthropic Claude API:
  - Claude Haiku  — conversational attorney responses + structured field extraction
  - Claude Sonnet — formal complaint document generation

Integrated features:
  - Multi-turn chat with session history (SQLite)
  - Jurisdiction filter (NY only)
  - Case type classifier (via Haiku extraction)
  - offer_complaint + case_type + required_elements returned in every response
    so the frontend can show the "Generate Complaint" button and pre-populate
    the intake form with the correct fields for the detected case type
"""

import os
import json
import uuid
import sqlite3
import logging
import asyncio
from datetime import datetime
from contextlib import contextmanager

from dotenv import load_dotenv
load_dotenv()   # Load .env before any os.getenv() call — must be first

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from element_extractor import extract_elements
from intake_router import router as intake_router, progress_router as intake_progress_router
from complaint_router import router as complaint_router
from docx_router import router as docx_router
from utils import normalize_case_fields   # shared — also imported by complaint_drafter
import anthropic as _anthropic_sdk

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Rate Limiter (slowapi)
# Protects the /questions endpoint from being hammered.
# Limits per IP: 30 requests/minute for chat, 10/minute for drafting.
# Adjust limits in the @limiter.limit() decorators below.
# ──────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────
app = FastAPI(title="Legal AI Assistant", version="3.2")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS origins ─────────────────────────────
# Comma-separated list of allowed frontend origins.
# Defaults to localhost:3000 for dev.
# Production: export ALLOWED_ORIGINS=https://yourapp.com,https://www.yourapp.com
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173,http://161.248.37.99:3002")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    # ALLOWED_ORIGINS is read from env var — defaults to localhost for dev.
    # Set ALLOWED_ORIGINS=https://yourapp.com in production.
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ──────────────────────────────────────────────
# API Key Auth Middleware
# Protects /intake/* and /draft/* with a Bearer token.
# /questions and /health are intentionally left public.
#
# If API_KEY env var is empty, auth is skipped entirely (dev mode).
# In production, every request to protected routes must include:
#   Authorization: Bearer <API_KEY>
# ──────────────────────────────────────────────
from fastapi.responses import JSONResponse, HTMLResponse
from pathlib import Path

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui():
    return Path("ui.html").read_text()

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    # Skip auth entirely if no API_KEY configured (dev mode)
    if not API_KEY:
        return await call_next(request)

    # Only protect intake and draft routes
    path = request.url.path
    protected = path.startswith("/intake") or path.startswith("/draft")
    if not protected:
        return await call_next(request)

    # Validate Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "Missing Authorization header. Use: Authorization: Bearer <token>"}
        )

    token = auth_header[len("Bearer "):].strip()
    if token != API_KEY:
        logger.warning(f"Invalid API key attempt | path={path} | ip={request.client.host}")
        return JSONResponse(
            status_code=403,
            content={"error": "Invalid API key."}
        )

    return await call_next(request)

# Day 5 — intake loop endpoints (/intake/start, /intake/{id}/provide, etc.)
app.include_router(intake_router)

# Progress endpoint (/case/{case_id}/progress)
app.include_router(intake_progress_router)

# Day 7 — complaint drafting endpoints (/draft/{case_id})
app.include_router(complaint_router)

# Day 8 — DOCX document generation (/document/{case_id})
app.include_router(docx_router)

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
DB_PATH              = os.getenv("DB_PATH", "chat_history.db")
MAX_HISTORY_MESSAGES = 5       # Last 5 user/assistant pairs
MAX_QUESTION_LENGTH  = 2000

# ── API key auth ──────────────────────────────
# Set API_KEY env var to require a Bearer token on all /intake/* and /draft/*
# endpoints. /questions and /health remain public so the chat UI works without auth.
# Leave unset (empty) during local development — auth middleware will be skipped.
#
# Production:  export API_KEY=your-secret-key-here
# Request:     Authorization: Bearer your-secret-key-here
API_KEY = os.getenv("API_KEY", "")
if not API_KEY:
    logger.warning(
        "API_KEY env var is not set — intake and draft endpoints are UNPROTECTED. "
        "Set API_KEY before deploying to production."
    )

# Anthropic Claude API — required for all AI features.
# Haiku handles conversation and field extraction; Sonnet handles complaint generation.
# Required: set ANTHROPIC_API_KEY in .env before starting the server.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

_anthropic_client: _anthropic_sdk.Anthropic | None = None
if ANTHROPIC_API_KEY:
    _anthropic_client = _anthropic_sdk.Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("Anthropic client initialised.")
else:
    logger.error(
        "ANTHROPIC_API_KEY is not set — all Claude API calls will fail. "
        "Set it in your .env file before starting the server."
    )

# All 7 case types with static element schemas are supported for complaint drafting.
# "other" is excluded — it has no element schema so no complaint can be structured.
COMPLAINT_SUPPORTED_CASES = [
    "eminent_domain",
    "contract_dispute",
    "personal_injury",
    "property_damage",
    "family_law",
    "criminal_defense",
    "employment_dispute",
]

# ──────────────────────────────────────────────
# Jurisdiction Filter
# ──────────────────────────────────────────────
# ══════════════════════════════════════════════
# Jurisdiction Filter — US / UK Allowlist
# ══════════════════════════════════════════════
#
# Design principle: allowlist beats blocklist.
#
# The system supports US and UK law only.
# Logic (three steps, evaluated in order):
#
#   Step 1 — US signal found  → ALLOW  (definitively US)
#   Step 2 — UK signal found  → ALLOW  (definitively UK)
#   Step 3 — Known foreign signal found → BLOCK
#   Default — no geographic signal at all → ALLOW (assume US)
#
# Why this ordering matters:
#   "New Mexico"  → hits US allowlist in step 1 → allowed  ✅
#                   (never reaches "mexico" in foreign list)
#   "New South Wales" → no US/UK match → hits "south wales" foreign?
#                   No — "new south wales" is in the foreign list.
#   "My client slipped" → no geographic signal → assumed US → allowed ✅
#
# The foreign signals list covers major non-US/UK countries + their top cities.
# It does NOT need to be exhaustive — unknown cities with no other context
# fall through to the default (assume US), which is the safe direction for
# a product used by US attorneys.
# ══════════════════════════════════════════════

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




_HAIKU_CONVERSE_SYSTEM = """You are Nyaay AI, a senior New York litigation assistant for licensed attorneys.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⛔ ABSOLUTE RULE — NEVER VIOLATE UNDER ANY CIRCUMSTANCE:
DO NOT write complaint text, numbered legal paragraphs, WHEREFORE clauses, court captions, or any draft document in this chat. EVER. The system generates complaints separately via a dedicated button. Writing complaint text here BREAKS the workflow and confuses the attorney.

When ready to draft → say only: "All set — click **Generate Complaint** above."
Then STOP. Write nothing else. Do not add paragraphs. Do not write a complaint.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROLE: Extract facts → identify gaps → offer draft when MVP met. Get to draftable state fast.

CORE PRINCIPLE:
Complaints are routinely filed with incomplete info. Damages = "to be determined at trial." Unknown defendants = "John Doe." Never block progress waiting for non-essentials.

MINIMUM VIABLE FACTS (offer draft immediately when these are met):
• personal_injury: plaintiff, defendant (or John Doe), incident date, location, injury
• employment_dispute: plaintiff, employer, what happened, when
• contract_dispute: plaintiff, defendant, what the contract was, how breached
• property_damage: plaintiff, defendant/description, what was damaged, when
• eminent_domain: plaintiff, government entity, property address
• criminal_defense: defendant name, charges, court
• family_law: petitioner, respondent, grounds/custody issue

WHEN USER PRESENTS A COMPLAINT DRAFT OR CASE SUMMARY:
Extract EVERYTHING from it — party names, dates, location, incident, injuries. Ask only about what is GENUINELY absent from the document. Do not ask about facts already stated.

INTELLIGENCE RULES:
1. Extract before asking — scan the full message for stated facts first
2. Infer from context — "rear-ended" = negligence_act captured; "fracture" = injury captured
3. Suggest alternatives — unknown defendant? "File as John Doe, serve DMV FOIA to identify."
4. Skip non-essentials — never ask for damages amount ("to be determined at trial")
5. Never re-ask captured facts

MISSING FIELDS FORMAT — always use this compact bullet format, never long questions:
**Still need:**
• plaintiff full name
• incident date

Show at most 3 missing fields per turn. Prioritize by legal importance.

WHEN USER SAYS "YES", "PROCEED", "GENERATE", "DRAFT IT", "GO AHEAD", OR ANY CONFIRMATION:
→ Respond with ONLY: "All set — click **Generate Complaint** above."
→ Do NOT write complaint text. Do NOT ask more questions. STOP after that one line.

WHEN ALL MVP FIELDS ARE MET:
→ Say: "I have what I need. Click **Generate Complaint** above, or type 'generate'."
→ Do NOT write complaint text.

LEGAL CONTEXT (mention once when relevant, skip if already stated):
• Slip and fall: constructive notice = hazard duration long enough for reasonable discovery
• Rear-end: NY presumption of negligence — defendant must explain or loses
• Wrongful termination: protected activity within 3 months = strong retaliation
• Dog bite: strict liability with prior knowledge of vicious propensity
• Tip theft: NYLL § 196-d, willful violation = double damages
• Pregnancy discrimination: NYCHRL + Title VII, strong if documented

SOL (mention once on case type identification):
• personal_injury / property_damage: 3 yrs (CPLR § 214)
• employment wages: 3 yrs; EEOC: 300 days
• contract (written): 6 yrs (CPLR § 213)
• eminent_domain: 1 yr + 90 days — ⚠️ URGENT, flag immediately
• family law: no SOL on divorce

RESPONSE FORMAT:
1. One sentence acknowledging new facts (skip if nothing new added)
2. Legal insight if genuinely useful (1 sentence max, only if not already stated)
3. If MVP fields missing: "**Still need:**" bullet list (max 3 items)
4. If MVP met: "I have what I need. Click **Generate Complaint** above."

JURISDICTION: Non-NY explicitly mentioned → "Nyaay handles NY matters only. NY connection?"
OFF-TOPIC: No legal content → "Describe your New York legal case."
TONE: Direct, collegial. Attorneys are busy. No fluff. No unnecessary words.
"""

_HAIKU_EXTRACT_SYSTEM = """You are a JSON extraction API. Output ONLY a valid JSON object. No commentary, no markdown, no explanation. Start your response with { and end with }.

Schema:
{"case_type": "personal_injury|employment_dispute|criminal_defense|contract_dispute|property_damage|eminent_domain|family_law|unknown", "extracted_fields": {}, "missing_fields": [], "ready_to_draft": false}

Rules:
- case_type: identify from conversation context. Use "unknown" only if truly unidentifiable.
- extracted_fields: every field explicitly stated OR clearly implied in the conversation. Extract aggressively.
- missing_fields: required fields not yet in extracted_fields for the identified case_type.
- ready_to_draft: true when MINIMUM VIABLE FACTS are present (not all fields). MVP per case type:
  personal_injury: plaintiff_name, incident_date, incident_location, injury_description (defendant_name is helpful but not blocking — file as John Doe if missing)
  employment_dispute: plaintiff_name, defendant_name, dispute_type, dispute_description
  contract_dispute: plaintiff_name, defendant_name, contract_description, breach_description
  property_damage: plaintiff_name, incident_date, damage_description
  eminent_domain: plaintiff_name, defendant_name, property_address
  criminal_defense: defendant_name, charges, court_name
  family_law: plaintiff_name, defendant_name

EXTRACTION RULES — extract from ALL message formats:
- "My client, Jane Doe" → plaintiff_name: "Jane Doe"
- "My client Jane Doe" → plaintiff_name: "Jane Doe"
- "the defendant, Whole Foods Market Group, Inc." → defendant_name: "Whole Foods Market Group, Inc."
- "JANE DOE, Plaintiff" (ALL CAPS caption) → plaintiff_name: "Jane Doe"
- "WHOLE FOODS MARKET GROUP, INC., Defendants" → defendant_name: "Whole Foods Market Group, Inc."
- "slipped and fell due to a hazardous liquid" → negligence_act: "failure to maintain safe premises / hazardous liquid on floor"
- "fractured wrist, lower back trauma" → injury_description: "fractured wrist, lower back trauma"
- "fractured wrist, lower back trauma, ongoing pain, requiring medical treatment" → medical_treatment: "received and ongoing"
- "constructive notice" mentioned → extract negligence context
- When user says incident was at "Whole Foods Market located in New York" → incident_location: "Whole Foods Market, New York"

CRITICAL: Use ONLY these exact field names as keys in extracted_fields. No synonyms, no variations.

personal_injury fields: plaintiff_name, defendant_name, incident_date, incident_location, injury_description, negligence_act, medical_treatment, medical_expenses, lost_wages, damages_claimed, witness_names, insurance_info
employment_dispute fields: plaintiff_name, defendant_name, employment_start_date, termination_date, job_title, dispute_type, dispute_description, hr_complaint_filed, eeoc_charge_filed, wages_owed, damages_claimed, witness_names
criminal_defense fields: defendant_name, arresting_agency, arrest_date, incident_date, charges, court_name, bail_amount, defense_theory, prior_record, evidence_description, witness_names
contract_dispute fields: plaintiff_name, defendant_name, contract_date, contract_description, written_contract, performance_by_plaintiff, breach_description, demand_letter_sent, contract_value, damages_claimed, witness_names
property_damage fields: plaintiff_name, defendant_name, incident_date, incident_location, property_description, damage_description, property_value, repair_cost, damages_claimed, police_report_number, insurance_claim, witness_names
eminent_domain fields: plaintiff_name, defendant_name, taking_date, property_address, public_use_stated, property_use, compensation_offered, fair_market_value, damages_claimed, appraisal_report
family_law fields: plaintiff_name, defendant_name, marriage_date, separation_date, grounds_for_divorce, children_names, custody_arrangement, spousal_support, property_list, debt_list

Examples of correct field name mapping:
- witnesses / witness list → witness_names
- insurance / policy number / insurer → insurance_info
- fired / terminated on → termination_date
- hired / started working → employment_start_date
- slip and fall / fell / slipped → negligence_act (describe the hazard)
- injuries / hurt / sustained → injury_description

Output ONLY the JSON object. Nothing else."""

# Minimum fields required before offering complaint draft.
# Intentionally small — complaints can be filed with partial info.
_MVP_FIELDS: dict[str, list[str]] = {
    "personal_injury":    ["plaintiff_name", "incident_date", "incident_location", "injury_description"],
    "employment_dispute": ["plaintiff_name", "defendant_name", "dispute_type", "dispute_description"],
    "contract_dispute":   ["plaintiff_name", "defendant_name", "contract_description", "breach_description"],
    "property_damage":    ["plaintiff_name", "incident_date", "damage_description"],
    "eminent_domain":     ["plaintiff_name", "defendant_name", "property_address"],
    "criminal_defense":   ["defendant_name", "charges", "court_name"],
    "family_law":         ["plaintiff_name", "defendant_name"],
}


# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"DB error: {e}")
        raise
    finally:
        conn.close()


def init_db():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                title      TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                timestamp  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS case_sessions (
                case_id          TEXT PRIMARY KEY,
                chat_session_id  TEXT NOT NULL,
                case_type        TEXT NOT NULL,
                required_fields  TEXT NOT NULL,
                provided_fields  TEXT NOT NULL,
                missing_fields   TEXT NOT NULL,
                force_draft       INTEGER DEFAULT 0,
                validation_result TEXT DEFAULT NULL,
                draft_generated   INTEGER DEFAULT 0,
                draft_text        TEXT DEFAULT NULL,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_session_id) REFERENCES sessions(id)
            )
        """)

        # ── Schema migrations ─────────────────────────────────────────────────
        # SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so we
        # attempt each ADD COLUMN and silently swallow the "duplicate column"
        # error. This is the standard pattern for safe SQLite migrations.
        # Any new column added to the CREATE TABLE above must also appear here
        # so that existing databases from earlier days get upgraded automatically
        # on the next server start — no manual SQL or DB wipes required.
        migrations = [
            # Day 6
            "ALTER TABLE case_sessions ADD COLUMN validation_result TEXT DEFAULT NULL",
            # Day 7
            "ALTER TABLE case_sessions ADD COLUMN draft_generated INTEGER DEFAULT 0",
            "ALTER TABLE case_sessions ADD COLUMN draft_text TEXT DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
                col = sql.split("ADD COLUMN")[1].strip().split()[0]
                logger.info(f"DB migration applied: added column '{col}'")
            except Exception:
                # Column already exists — this is expected on all but the first run
                pass

    logger.info("Database initialised.")


init_db()


# ──────────────────────────────────────────────
# Pydantic Models
# QuestionResponse includes offer_complaint + case_type + required_elements so the
# frontend always knows whether to show the "Generate Complaint" button and which
# fields to collect during the Day 5 intake loop.
# ──────────────────────────────────────────────
class QuestionRequest(BaseModel):
    question:   str
    session_id: str | None = None

    @field_validator("question")
    @classmethod
    def validate_question(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty.")
        if len(v) > MAX_QUESTION_LENGTH:
            raise ValueError(f"Question exceeds {MAX_QUESTION_LENGTH} characters.")
        return v


class QuestionResponse(BaseModel):
    answer:            str
    session_id:        str
    offer_complaint:   bool       = False  # tells frontend to show "Generate Complaint" button
    case_type:         str        = None   # e.g. "eminent_domain", "personal_injury", etc.
    required_elements: list[dict] = []     # flat element list for the detected case type
    sections:          dict       = {}     # ordered section_name → [field_id, ...] for UI grouping
    # True when classifier confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD.
    # Frontend should show: "We think this is a <case_type> case — is that correct?"
    # before proceeding to intake, rather than silently using a potentially wrong label.
    classification_low_confidence: bool = False


# ──────────────────────────────────────────────
# Session Helpers
# ──────────────────────────────────────────────
def get_or_create_session(conn, session_id: str = None, title: str = "New Chat") -> str:
    if session_id:
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            return session_id
        logger.warning(f"Session {session_id} not found — creating new one.")

    new_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (id, title, created_at) VALUES (?, ?, ?)",
        (new_id, title, datetime.utcnow())
    )
    logger.info(f"Created session: {new_id}")
    return new_id


def get_session_messages(conn, session_id: str, limit: int = MAX_HISTORY_MESSAGES):
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_message(conn, session_id: str, role: str, content: str):
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, datetime.utcnow())
    )


# ──────────────────────────────────────────────
# Case Session Helpers
# Used by complaint_drafter.py (Day 7) and any future endpoint that needs
# to read or update a case session without going through intake_router.py.
# Keeping these in app.py avoids circular imports — the drafter only needs
# to import from app.py, not from the intake router.
# ──────────────────────────────────────────────
def get_case_session(case_id: str) -> dict:
    """
    Fetch a case_session row by case_id and return it as a plain dict.

    Called by the complaint drafting engine to load the full case state
    before building the Claude prompt.

    Returns:
        {
            "case_id":         str,
            "case_type":       str,
            "provided_fields": dict,   # { element_id: value }
            "missing_fields":  list,   # element IDs still missing
            "required_fields": list,   # all element IDs for this case type
            "force_draft":     bool,
            "draft_generated": bool,
            "draft_text":      str | None,
        }

    Raises:
        HTTPException 404 if case_id does not exist.

    Example:
        session = get_case_session("abc-123")
        # → { "case_type": "personal_injury",
        #     "provided_fields": {"plaintiff_name": "John Doe", ...},
        #     "force_draft": False,
        #     "draft_generated": False,
        #     "draft_text": None }
    """
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM case_sessions WHERE case_id = ?",
            (case_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Case session '{case_id}' not found.")

    provided_fields = json.loads(row["provided_fields"])
    # Strip internal source-tracking metadata — complaint_drafter and
    # normalize_case_fields expect a flat { field_id: string_value } dict.
    provided_fields.pop("__sources__", None)

    return {
        "case_id":         row["case_id"],
        "case_type":       row["case_type"],
        "chat_session_id": row["chat_session_id"],
        "provided_fields": provided_fields,
        "missing_fields":  json.loads(row["missing_fields"]),
        "required_fields": json.loads(row["required_fields"]),
        "force_draft":     bool(row["force_draft"]),
        "draft_generated": bool(row["draft_generated"]),
        "draft_text":      row["draft_text"],
    }


# ──────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────
@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
@app.get("/ui.html", response_class=HTMLResponse, include_in_schema=False)
async def test_ui():
    ui_path = os.path.join(os.path.dirname(__file__), "ui.html")
    with open(ui_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health_check():
    return {
        "status":    "healthy",
        "api":       "claude",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ──────────────────────────────────────────────
# Haiku helper functions
# ──────────────────────────────────────────────

def _get_case_state_for_session(conn, chat_session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM case_sessions WHERE chat_session_id = ? ORDER BY created_at DESC LIMIT 1",
        (chat_session_id,)
    ).fetchone()
    if not row:
        return None
    return {
        "case_type":       row["case_type"],
        "provided_fields": json.loads(row["provided_fields"])  if row["provided_fields"]  else {},
        "missing_fields":  json.loads(row["missing_fields"])   if row["missing_fields"]   else [],
        "required_fields": json.loads(row["required_fields"])  if row["required_fields"]  else [],
    }


def _upsert_case_session(conn, chat_session_id: str, case_type: str,
                          extracted_fields: dict, missing_fields: list) -> None:
    elements_result = extract_elements(case_type, None, None)
    required_fields = [e["id"] for e in elements_result.get("elements", [])]
    now = datetime.utcnow().isoformat()

    existing = conn.execute(
        "SELECT * FROM case_sessions WHERE chat_session_id = ? ORDER BY created_at DESC LIMIT 1",
        (chat_session_id,)
    ).fetchone()

    if existing:
        current_provided = json.loads(existing["provided_fields"]) if existing["provided_fields"] else {}
        for key, value in extracted_fields.items():
            if value and value != "[UNKNOWN]":
                current_provided[key] = value
        new_missing = [f for f in required_fields if not current_provided.get(f)]
        conn.execute(
            """UPDATE case_sessions
               SET case_type=?, provided_fields=?, missing_fields=?, required_fields=?, updated_at=?
               WHERE case_id=?""",
            (case_type, json.dumps(current_provided), json.dumps(new_missing),
             json.dumps(required_fields), now, existing["case_id"])
        )
    else:
        case_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO case_sessions
               (case_id, chat_session_id, case_type, required_fields, provided_fields,
                missing_fields, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, chat_session_id, case_type, json.dumps(required_fields),
             json.dumps(extracted_fields), json.dumps(missing_fields), now, now)
        )


async def _haiku_converse(question: str, history: list, case_state: dict | None) -> str:
    if not _anthropic_client:
        return "Anthropic API key not configured. Please contact your administrator."

    state_context = ""
    if case_state and case_state.get("provided_fields"):
        captured = case_state["provided_fields"]
        missing  = case_state["missing_fields"]
        state_context = (
            f"\n\n[CURRENT CASE STATE — use this to avoid re-asking captured fields]\n"
            f"Case type: {case_state.get('case_type', 'not yet identified')}\n"
            f"Captured: {json.dumps(captured)}\n"
            f"Still missing: {', '.join(missing) if missing else 'none'}\n"
        )

    messages = []
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": question + state_context})

    try:
        resp = await asyncio.to_thread(
            _anthropic_client.messages.create,
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=_HAIKU_CONVERSE_SYSTEM,
            messages=messages,
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"Haiku converse error: {e}")
        return "I'm having trouble connecting right now. Please try again in a moment."


async def _haiku_extract(recent_turns: list) -> dict:
    _default = {"case_type": "unknown", "extracted_fields": {}, "missing_fields": [], "ready_to_draft": False}
    if not _anthropic_client:
        return _default

    conversation_text = "\n".join(
        f"{t['role'].upper()}: {t['content']}" for t in recent_turns[-6:]
    )

    raw = ""
    try:
        resp = await asyncio.to_thread(
            _anthropic_client.messages.create,
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=_HAIKU_EXTRACT_SYSTEM,
            messages=[
                {"role": "user",      "content": conversation_text},
                {"role": "assistant", "content": "{"},  # force JSON start
            ],
        )
        raw = "{" + resp.content[0].text.strip()
        # Strip markdown code fences (```json ... ```)
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:]).rstrip("`").strip()
        # Fallback: find first JSON object in response if Haiku added surrounding text
        if not raw.startswith("{"):
            import re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            raw = m.group(0) if m else raw
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Haiku extract returned non-JSON: {raw[:120]}")
        return _default
    except Exception as e:
        logger.error(f"Haiku extract error: {e}")
        return _default


# ──────────────────────────────────────────────
# POST /questions — Main endpoint
#
# Flow:
#   1. Jurisdiction check (fast reject, no model needed)
#   2. Load session + history + case_state from DB
#   3. Haiku CONVERSE — attorney-facing conversational response
#   4. Persist messages to DB
#   5. Haiku EXTRACT — structured JSON field extraction
#   6. Upsert case_session in DB with extracted fields
#   7. Return response + offer_complaint flag
# ──────────────────────────────────────────────
@app.post("/questions", response_model=QuestionResponse)
@limiter.limit("30/minute")
async def post_question(body: QuestionRequest, request: Request):
    request_data = body
    logger.info(f"Question: {request_data.question[:100]}...")

    # Fast reject: unsupported jurisdiction (no model needed)
    if is_unsupported_jurisdiction(request_data.question):
        with get_db_connection() as conn:
            session_id = get_or_create_session(conn, request_data.session_id, title=request_data.question[:50])
            save_message(conn, session_id, "user",      request_data.question)
            save_message(conn, session_id, "assistant", UNSUPPORTED_RESPONSE)
        logger.info(f"Non-NY jurisdiction blocked | session: {session_id}")
        return QuestionResponse(
            answer=UNSUPPORTED_RESPONSE,
            session_id=session_id,
            offer_complaint=False,
            case_type="unsupported",
            required_elements=[],
            sections={},
        )

    # Load session, history, and current case state
    with get_db_connection() as conn:
        session_id        = get_or_create_session(conn, request_data.session_id, title=request_data.question[:50])
        previous_messages = get_session_messages(conn, session_id)
        case_state        = _get_case_state_for_session(conn, session_id)

    # Call 1: Haiku generates the attorney-facing conversational response
    answer = await _haiku_converse(request_data.question, previous_messages, case_state)

    # Persist messages
    with get_db_connection() as conn:
        save_message(conn, session_id, "user",      request_data.question)
        save_message(conn, session_id, "assistant", answer)
        if not previous_messages:
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (request_data.question[:50], session_id)
            )

    # Call 2: Haiku extracts structured fields from the conversation
    recent_turns = previous_messages[-4:] + [
        {"role": "user",      "content": request_data.question},
        {"role": "assistant", "content": answer},
    ]
    extraction = await _haiku_extract(recent_turns)

    case_type        = extraction.get("case_type", "unknown")
    extracted_fields = extraction.get("extracted_fields", {})
    missing_fields   = extraction.get("missing_fields", [])
    ready_to_draft   = extraction.get("ready_to_draft", False)

    # Update case session in DB — use extracted case_type if known, else fall back to DB state
    effective_case_type = case_type if case_type and case_type != "unknown" else None
    if not effective_case_type and case_state:
        effective_case_type = case_state.get("case_type")

    if effective_case_type and effective_case_type != "unknown":
        with get_db_connection() as conn:
            _upsert_case_session(conn, session_id, effective_case_type, extracted_fields, missing_fields)

    # Re-read DB state to get authoritative picture after merge
    with get_db_connection() as conn:
        updated_state = _get_case_state_for_session(conn, session_id)

    if updated_state:
        effective_case_type = updated_state["case_type"]
        provided = updated_state.get("provided_fields") or {}
        mvp = _MVP_FIELDS.get(effective_case_type, [])
        ready_to_draft = bool(mvp) and all(provided.get(f) for f in mvp)

    # Get required elements for frontend (static lookup — no model needed)
    elements_result   = extract_elements(effective_case_type or "other", None, None)
    required_elements = elements_result.get("elements", [])
    sections          = elements_result.get("sections", {})

    offer_complaint = ready_to_draft and effective_case_type in COMPLAINT_SUPPORTED_CASES

    logger.info(
        f"Done | session={session_id} | case_type={effective_case_type} | "
        f"ready_to_draft={ready_to_draft} | fields_extracted={len(extracted_fields)}"
    )

    return QuestionResponse(
        answer=answer,
        session_id=session_id,
        offer_complaint=offer_complaint,
        case_type=effective_case_type or "other",
        required_elements=required_elements,
        sections=sections,
        classification_low_confidence=False,
    )


# ──────────────────────────────────────────────
# Session & History Endpoints
# ──────────────────────────────────────────────
@app.get("/sessions")
async def get_sessions():
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC"
        ).fetchall()
    return [{"id": r["id"], "title": r["title"], "created_at": r["created_at"]} for r in rows]


@app.get("/history/{session_id}")
async def get_history(session_id: str):
    with get_db_connection() as conn:
        if not conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Session not found.")
        messages = conn.execute(
            "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        ).fetchall()
    return [{"role": m["role"], "content": m["content"], "timestamp": m["timestamp"]} for m in messages]


@app.post("/history/{session_id}/clear")
async def clear_history(session_id: str):
    with get_db_connection() as conn:
        if not conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Session not found.")
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    logger.info(f"Cleared history | session: {session_id}")
    return {"message": "Chat history cleared.", "session_id": session_id}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    with get_db_connection() as conn:
        if not conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Session not found.")
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?",         (session_id,))
    logger.info(f"Deleted session: {session_id}")
    return {"message": "Session deleted.", "session_id": session_id}


if __name__ == "__main__":
    import uvicorn
    import signal
    import sys

    def shutdown_handler(signum, frame):
        print("\nShutting down server gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, shutdown_handler)  # kill command

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9000,
        workers=1,
        timeout_keep_alive=5
    )