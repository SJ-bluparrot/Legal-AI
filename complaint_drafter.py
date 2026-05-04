"""
complaint_drafter.py — Complaint Drafting Engine
--------------------------------------------------
Uses Claude Sonnet to generate a formally structured legal complaint
from the case session data collected during intake.

Complaint structure produced for every case type:
    1.  Caption         — Court name, case title, case number placeholder
    2.  Parties         — Plaintiff and defendant with addresses
    3.  Jurisdiction    — Subject matter and personal jurisdiction
    4.  Venue           — Why this court is the correct venue
    5.  Factual Allegations — Numbered paragraphs of the facts
    6.  Cause(s) of Action  — Legal theories with elements stated
    7.  Prayer for Relief   — Specific relief requested
    8.  Jury Demand     — If applicable to the case type
    9.  Signature Block — Attorney certification line

Per-case-type prompts:
    Each case type gets a prompt tailored to its legal doctrine and the
    specific elements collected during intake. A personal injury complaint
    is structured around duty/breach/causation/damages. A contract dispute
    uses formation/breach/damages. An eminent domain claim uses the Takings
    Clause. This is not a one-size-fits-all prompt.

Draft lock:
    If draft_generated = 1 in the DB, the cached draft_text is returned
    immediately — no Claude API call is made. This prevents duplicate
    charges and ensures the attorney always gets back the same document
    on retries or page reloads.

[UNKNOWN] handling:
    Missing required fields are replaced with [UNKNOWN] by
    normalize_case_fields() in app.py before the prompt is built.
    Claude is explicitly instructed to use [UNKNOWN] as-is rather than
    inventing values — keeping gaps visible and attorney-fixable.

Usage:
    from complaint_drafter import draft_complaint

    result = draft_complaint(case_id="abc-123")
    # → {
    #     "case_id":      "abc-123",
    #     "case_type":    "personal_injury",
    #     "complaint":    "IN THE UNITED STATES DISTRICT COURT...",
    #     "from_cache":   False,
    #     "word_count":   842,
    #     "unknown_count": 0
    # }
"""

import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

import anthropic

from utils import normalize_case_fields, build_court_caption   # shared utilities — single definition in utils.py

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL        = "claude-sonnet-4-6"
HAIKU_CLEANUP_MODEL = "claude-haiku-4-5-20251001"  # Used for complaint cleanup
CLAUDE_MAX_TOKENS  = 1800                  # Capped at 1800 — safer under load; prompt tokens
                                            # + output tokens must stay within context window
CLAUDE_TIMEOUT_SEC = 60                    # 60s timeout — Claude Sonnet typically responds
                                            # in 10–30s; 30s was too tight under load

DB_PATH = os.getenv("DB_PATH", "chat_history.db")

# ──────────────────────────────────────────────
# Singleton Claude client
# Created once at module load — not inside _call_claude() on every request.
# Saves 50–100ms per call (avoids TCP handshake + TLS setup on each request).
# The client is stateless so it is safe to share across requests.
# Initialised as None if the API key is missing — _call_claude() checks and
# raises a clear RuntimeError before attempting any network call.
# ──────────────────────────────────────────────
_claude_client: anthropic.Anthropic | None = None

if ANTHROPIC_API_KEY:
    _claude_client = anthropic.Anthropic(
        api_key = ANTHROPIC_API_KEY,
        timeout = CLAUDE_TIMEOUT_SEC,
    )
    logger.info("Anthropic client initialised (singleton).")
else:
    logger.warning(
        "ANTHROPIC_API_KEY not set — Claude client not initialised. "
        "Complaint drafting will fail until the key is configured."
    )

# ──────────────────────────────────────────────
# DB helper (mirrors app.py pattern — no circular import)
# ──────────────────────────────────────────────
@contextmanager
def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Drafter DB error: {e}")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════
# PER-CASE-TYPE PROMPT TEMPLATES
# ══════════════════════════════════════════════
# Each template receives a filled fields dict (with [UNKNOWN] placeholders)
# and returns the user-turn content sent to Claude.
#
# Design principles:
#   - Numbered allegations — courts expect numbered paragraphs
#   - Elements stated explicitly — each cause of action lists its elements
#   - Jurisdictional language — diversity jurisdiction / federal question
#     placeholders so the attorney fills in the right court
#   - Prayer for relief is specific, not generic ("compensatory damages
#     in an amount to be proven at trial" not "damages")
#   - [UNKNOWN] appears where data is missing — Claude is told not to invent
# ──────────────────────────────────────────────

def _system_prompt() -> str:
    """
    System prompt shared across all case types.
    Enforces NY CPLR Verified Complaint format.
    """
    return (
        "You are a New York civil litigation attorney drafting a formal Verified Complaint "
        "for filing in New York Supreme Court under the CPLR.\n\n"

        "MANDATORY FORMAT RULES — NY CPLR STANDARD:\n"
        "1. DO NOT use section headers. Real NY CPLR complaints contain no headers like "
        "'PARTIES', 'FACTUAL ALLEGATIONS', 'JURISDICTION AND VENUE', 'NATURE OF THE ACTION', "
        "or 'CAUSES OF ACTION'. The body is flowing numbered paragraphs only.\n"
        "2. Start with the court caption block (court name, dashed separator lines, party block). "
        "Then a blank line, then the intro sentence:\n"
        "   'Plaintiff, [NAME IN ALL CAPS], by [his/her] attorneys, [FIRM NAME], as and for "
        "the Verified Complaint, herein alleges the following:'\n"
        "3. Every allegation is a numbered paragraph, numbered from 1 sequentially. Never skip.\n"
        "4. Every numbered paragraph begins with 'That at all times hereinafter mentioned, ' "
        "EXCEPT paragraphs describing a specific event on a specific date — those begin:\n"
        "   'That on or about the [ordinal] day of [Month] [Year], '\n"
        "5. For defendant facts use: 'upon information and belief, the defendant, [NAME IN ALL CAPS],'\n"
        "6. After all numbered paragraphs, write the WHEREFORE clause with this exact text:\n"
        "   'WHEREFORE, Plaintiff demands judgment against the Defendant as follows:\n\n"
        "   A sum that exceeds the jurisdictional limits of all lower courts which would otherwise "
        "have jurisdiction in this matter, together with the costs and disbursements of the action.'\n"
        "7. After WHEREFORE, write the dated signature block:\n"
        "   'Dated: [CITY], New York\n           [DATE]\n\n[FIRM NAME]\nAttorneys for Plaintiff\n\n"
        "By: [ATTORNEY NAME], Esq.\n[ADDRESS]\n[PHONE]'\n"
        "8. Write [UNKNOWN] exactly as-is for any missing field. Never invent information.\n"
        "9. Use formal NY legal language: 'aforementioned', 'herein', 'hereinafter', 'situs', "
        "'hereinafter mentioned', 'wanton, reckless and careless'.\n"
        "10. Write all party names in ALL CAPS after first introduction.\n"
        "11. Output ONLY the complaint text. No commentary, no explanation, no markdown.\n"
        "12. JURISDICTION vs VENUE — critical distinction:\n"
        "    - DO NOT cite CPLR § 503 for jurisdiction. CPLR § 503 governs VENUE only "
        "(where the trial is held, based on party residence or where events occurred).\n"
        "    - The Supreme Court's jurisdiction comes from NY Constitution Article VI § 7 "
        "(court of general original jurisdiction). Do NOT cite CPLR § 503(a)(1) or (a)(2) "
        "for jurisdiction — those subsections do not exist.\n"
        "    - For venue, cite only: CPLR § 503(a).\n"
        "13. STATUTE ACCURACY — only cite statutes that actually apply to the case type:\n"
        "    - NY General Obligations Law § 11-101 (Dram Shop Act) applies ONLY when a "
        "vendor illegally sold alcohol to an intoxicated person who then caused injury. "
        "NEVER cite it for property damage, motor vehicle accidents, or general negligence.\n"
        "    - Insurance Law §§ 5102(d) and 5104 apply ONLY to motor vehicle personal injury "
        "cases. Do not cite them for property damage, contract, or employment cases.\n"
        "    - CPLR §§ 1601-1602 (limited liability) apply only to personal injury/tort cases."
    )


def _build_personal_injury_prompt(fields: dict) -> str:
    plaintiff  = fields.get('plaintiff_name',    '[UNKNOWN]')
    defendant  = fields.get('defendant_name',    '[UNKNOWN]')
    date       = fields.get('incident_date',     '[UNKNOWN]')
    location   = fields.get('incident_location', '[UNKNOWN]')
    injury     = fields.get('injury_description','[UNKNOWN]')
    negligence = fields.get('negligence_act',    '[UNKNOWN]')
    medical    = fields.get('medical_treatment', '[UNKNOWN]')
    expenses   = fields.get('medical_expenses',  'to be determined at trial')
    wages      = fields.get('lost_wages',        'to be determined at trial')

    # Derive county from location for the caption venue block
    location_lower = location.lower()
    if any(x in location_lower for x in ['manhattan', 'new york county']):
        county = 'NEW YORK'
    elif any(x in location_lower for x in ['brooklyn', 'kings']):
        county = "KINGS"
    elif 'queens' in location_lower:
        county = 'QUEENS'
    elif 'bronx' in location_lower:
        county = 'BRONX'
    elif 'staten island' in location_lower or 'richmond' in location_lower:
        county = 'RICHMOND'
    else:
        county = 'NEW YORK'

    dash_line = "-" * 73 + "X"

    return f"""Draft a formal Verified Complaint for a NY Supreme Court personal injury case.

CASE INFORMATION:
- Plaintiff: {plaintiff}
- Defendant: {defendant}
- Date of incident: {date}
- Location: {location}
- How it happened: {negligence}
- Injuries: {injury}
- Medical treatment: {medical}
- Medical expenses: {expenses}
- Lost wages: {wages}

Begin with this court caption block exactly as shown (using the party names above):

SUPREME COURT OF THE STATE OF NEW YORK
COUNTY OF {county}
{dash_line}
{plaintiff.upper()},
                              Plaintiff,

         -against-                                VERIFIED COMPLAINT

{defendant.upper()},

                                                  Index No.:
                              Defendants.
{dash_line}

Then write the intro sentence:
"Plaintiff, {plaintiff.upper()}, by [his/her] attorneys, [FIRM NAME], as and for the Verified Complaint, herein alleges the following:"

Then write EXACTLY these numbered paragraphs (adapt the content to the case facts above):

1. Plaintiff's residency: "That at all times hereinafter mentioned, the Plaintiff is and was a resident of the County of {county.title()}, City and State of New York."

2. Defendant's residency: "That at all times hereinafter mentioned, upon information and belief, the defendant, {defendant.upper()}, was and still is a resident of [city and state — infer from location if possible, else [UNKNOWN]]."

3. If motor vehicle case — defendant as owner/operator: "That at all times hereinafter mentioned, upon information and belief, the defendant, {defendant.upper()}, was the owner and/or operator of a motor vehicle [description if available] for the State of New York." (Skip if not a vehicle case.)

4. Location as public thoroughfare: "That at all times hereinafter mentioned, {location}, located in the County of {county.title()}, City and State of New York, was and still is a public highway and thoroughfare and was the situs of the accident herein."

5. The incident: "That on or about the [ordinal date] of {date}, the aforementioned [vehicle/parties] were in contact with each other at the aforementioned location."

6. Causation: "The contact and injuries alleged herein were caused by the negligent, wanton, reckless and careless acts of the Defendant herein."

7. Specific negligence — a single long paragraph listing ALL applicable failures based on "{negligence}": failing to stop; failing to see what was there to be seen; failing to maintain proper control; following at unsafe distance; failing to keep a proper lookout; in striking the Plaintiff; in violating the rules of the road; in failing to observe that degree of caution, prudence and care which was reasonable and proper; in acting with reckless disregard for the safety of others; in failing to keep alert and attentive; and in other ways negligent, wanton, reckless and careless.

8. "The limited liability provisions of CPLR §1601 do not apply pursuant to the exceptions of CPLR §1602 (6) and (7)."

9. Injuries: "That by reason of the foregoing, the Plaintiff was caused to sustain severe and serious personal injuries to the mind and body, including {injury}, some of which, upon information and belief, are permanent with permanent effects of pain, disability, disfigurement, and loss of body function."

10. Economic losses: "Further, the Plaintiff was caused to expend and become obligated for diverse sums of money for the purpose of obtaining medical care and/or cure in an effort to alleviate the suffering and ills sustained as a result of the accident; the Plaintiff further was caused to lose substantial periods of time from [his/her] normal vocation, and upon information and belief, may continue in that way into the future and suffer similar losses."

11. Serious injury threshold: "The Plaintiff sustained a serious injury, as defined in the Insurance Law Section 5102(d) for the State of New York, and economic losses in excess of \\"basic economic loss\\" as set forth in Insurance Law Sections 5102 and 5104."

12. Jurisdictional amount: "That by reason of the foregoing, the Plaintiff has been damaged in a sum that exceeds the jurisdictional limits of all lower courts which would otherwise have jurisdiction of this matter."

Then write the WHEREFORE clause followed by a dated signature block and defendant addresses."""


def _build_eminent_domain_prompt(fields: dict) -> str:
    return f"""Draft a formal civil complaint for an eminent domain / inverse condemnation case.

CASE INFORMATION:
- Property owner (Plaintiff): {fields.get('plaintiff_name', '[UNKNOWN]')}
- Government entity (Defendant): {fields.get('defendant_name', '[UNKNOWN]')}
- Date of taking: {fields.get('taking_date', '[UNKNOWN]')}
- Property address / description: {fields.get('property_address', '[UNKNOWN]')}
- Stated public use by government: {fields.get('public_use_stated', '[UNKNOWN]')}
- Prior use of property: {fields.get('property_use', '[UNKNOWN]')}
- Compensation offered by government: {fields.get('compensation_offered', '[UNKNOWN]')}
- Estimated fair market value: {fields.get('fair_market_value', '[UNKNOWN]')}
- Additional damages: {fields.get('damages_claimed', '[UNKNOWN]')}
- Appraisal obtained: {fields.get('appraisal_report', '[UNKNOWN]')}

The complaint must include:
1. Caption (court, parties, case number as [CASE NO. TO BE ASSIGNED])
2. PARTIES section
3. JURISDICTION AND VENUE section citing 42 U.S.C. § 1983 or applicable state law
4. FACTUAL ALLEGATIONS section with numbered paragraphs covering:
   - Plaintiff's ownership and use of the property
   - The government's taking action and stated justification
   - That the taking constitutes a "taking" under the Fifth Amendment
   - The government's inadequate compensation offer
   - The property's fair market value
5. CAUSE OF ACTION: INVERSE CONDEMNATION / JUST COMPENSATION
   - Cite the Fifth Amendment Takings Clause
   - State that just compensation has not been paid
6. CAUSE OF ACTION: VIOLATION OF DUE PROCESS (if applicable)
7. PRAYER FOR RELIEF requesting:
   - Just compensation equal to fair market value
   - Consequential damages (relocation costs, business losses)
   - Pre-judgment interest from date of taking
   - Attorney's fees and costs
   - Such other relief as the Court deems just
8. Signature block with [ATTORNEY NAME], [BAR NUMBER], [FIRM NAME], [ADDRESS], [DATE]"""


def _build_contract_dispute_prompt(fields: dict) -> str:
    return f"""Draft a formal civil complaint for a breach of contract case.

CASE INFORMATION:
- Plaintiff: {fields.get('plaintiff_name', '[UNKNOWN]')}
- Defendant: {fields.get('defendant_name', '[UNKNOWN]')}
- Contract date: {fields.get('contract_date', '[UNKNOWN]')}
- Contract description: {fields.get('contract_description', '[UNKNOWN]')}
- Written contract exists: {fields.get('written_contract', '[UNKNOWN]')}
- Plaintiff's performance: {fields.get('performance_by_plaintiff', '[UNKNOWN]')}
- Description of breach: {fields.get('breach_description', '[UNKNOWN]')}
- Demand letter sent: {fields.get('demand_letter_sent', '[UNKNOWN]')}
- Contract value: {fields.get('contract_value', '[UNKNOWN]')}
- Damages claimed: {fields.get('damages_claimed', '[UNKNOWN]')}
- Witnesses: {fields.get('witness_names', '[UNKNOWN]')}

The complaint must include:
1. Caption (court, parties, case number as [CASE NO. TO BE ASSIGNED])
2. PARTIES section
3. JURISDICTION AND VENUE section
4. FACTUAL ALLEGATIONS section with numbered paragraphs covering:
   - Formation of the contract (offer, acceptance, consideration)
   - Material terms of the agreement
   - Plaintiff's full performance or substantial performance
   - Defendant's breach (what was not done / done wrong)
   - Notice of breach and demand for cure (if applicable)
   - Resulting damages
5. CAUSE OF ACTION: BREACH OF CONTRACT
   - All four elements: existence of contract, plaintiff's performance,
     defendant's breach, resulting damages
6. CAUSE OF ACTION: BREACH OF IMPLIED COVENANT OF GOOD FAITH AND FAIR DEALING (if applicable)
7. PRAYER FOR RELIEF requesting:
   - Compensatory damages in the amount of {fields.get('damages_claimed', '[UNKNOWN]')}
   - Consequential and incidental damages
   - Pre-judgment interest
   - Attorney's fees (if contractually or statutorily available)
   - Costs of suit
8. JURY DEMAND
9. Signature block with [ATTORNEY NAME], [BAR NUMBER], [FIRM NAME], [ADDRESS], [DATE]"""


def _build_property_damage_prompt(fields: dict) -> str:
    return f"""Draft a formal civil complaint for a property damage / conversion case.

CASE INFORMATION:
- Plaintiff (property owner): {fields.get('plaintiff_name', '[UNKNOWN]')}
- Defendant: {fields.get('defendant_name', '[UNKNOWN]')}
- Date of incident: {fields.get('incident_date', '[UNKNOWN]')}
- Location of incident: {fields.get('incident_location', '[UNKNOWN]')}
- Property description: {fields.get('property_description', '[UNKNOWN]')}
- Description of damage / theft: {fields.get('damage_description', '[UNKNOWN]')}
- Property value: {fields.get('property_value', '[UNKNOWN]')}
- Repair / replacement cost: {fields.get('repair_cost', '[UNKNOWN]')}
- Total damages claimed: {fields.get('damages_claimed', '[UNKNOWN]')}
- Police report number: {fields.get('police_report_number', '[UNKNOWN]')}
- Insurance claim status: {fields.get('insurance_claim', '[UNKNOWN]')}
- Witnesses: {fields.get('witness_names', '[UNKNOWN]')}

The complaint must include:
1. Caption (court, parties, case number as [CASE NO. TO BE ASSIGNED])
2. PARTIES section
3. JURISDICTION AND VENUE section
4. FACTUAL ALLEGATIONS section with numbered paragraphs covering:
   - Plaintiff's ownership of the property
   - The incident: date, location, how damage / theft occurred
   - Defendant's intentional or negligent act causing damage
   - Value of damaged / stolen property
   - Costs to repair or replace
5. CAUSE OF ACTION — choose based on damage_description:
   - Intentional damage or theft → TRESPASS TO CHATTELS and/or CONVERSION
   - Accidental damage → NEGLIGENCE (duty / breach / causation / damages)
   CRITICAL: Do NOT cite General Obligations Law § 11-101 (Dram Shop Act) — that law
   applies ONLY when a vendor illegally sold alcohol to an intoxicated person who then
   caused harm. It has no application to property damage cases.
   Do NOT cite Insurance Law §§ 5102 or 5104 — those are motor vehicle injury statutes only.
6. WHEREFORE clause requesting:
   - Compensatory damages equal to property value and/or repair costs
   - Return of property or its equivalent value
   - Consequential damages
   - Costs and disbursements of the action
7. Signature block with [ATTORNEY NAME], [FIRM NAME], [ADDRESS], [DATE]"""


def _build_family_law_prompt(fields: dict) -> str:
    return f"""Draft a formal petition for dissolution of marriage (divorce) and related relief.

CASE INFORMATION:
- Petitioner: {fields.get('plaintiff_name', '[UNKNOWN]')}
- Respondent: {fields.get('defendant_name', '[UNKNOWN]')}
- Date of marriage: {fields.get('marriage_date', '[UNKNOWN]')}
- Date of separation: {fields.get('separation_date', '[UNKNOWN]')}
- State of jurisdiction: {fields.get('jurisdiction_state', '[UNKNOWN]')}
- Grounds for divorce: {fields.get('grounds_for_divorce', '[UNKNOWN]')}
- Children's names and ages: {fields.get('children_names', '[UNKNOWN]')}
- Custody arrangement sought: {fields.get('custody_arrangement', '[UNKNOWN]')}
- Spousal support requested: {fields.get('spousal_support', '[UNKNOWN]')}
- Marital property: {fields.get('property_list', '[UNKNOWN]')}
- Marital debts: {fields.get('debt_list', '[UNKNOWN]')}

The petition must include:
1. Caption (court, parties, case number as [CASE NO. TO BE ASSIGNED])
2. PARTIES section with residency allegations establishing jurisdiction
3. JURISDICTION AND VENUE section (residency requirements, state statute citation as [STATE STATUTE])
4. FACTUAL ALLEGATIONS section covering:
   - Date and place of marriage
   - Date of separation and length of marriage
   - Residency requirements met
   - Grounds for dissolution
5. MINOR CHILDREN section (if applicable):
   - Names, ages, current residence
   - Proposed custody arrangement
   - Child support request
6. PROPERTY AND DEBTS section:
   - Request for equitable division
   - Identification of significant marital assets and debts
7. SPOUSAL SUPPORT section (if applicable)
8. PRAYER FOR RELIEF requesting:
   - Dissolution of the marriage
   - Legal custody and physical custody order
   - Child support per state guidelines
   - Equitable division of marital property and debts
   - Spousal support (if requested)
   - Restoration of prior name (if requested)
   - Such other relief as the Court deems just
9. Signature block with [ATTORNEY NAME], [BAR NUMBER], [FIRM NAME], [ADDRESS], [DATE]"""


def _build_criminal_defense_prompt(fields: dict) -> str:
    return f"""Draft a formal criminal defense motion to dismiss or demurrer (pre-trial motion challenging the charges).

NOTE: Criminal defense complaints are typically motions filed on behalf of the defendant.
Draft this as a Motion to Dismiss for insufficient evidence / lack of probable cause.

CASE INFORMATION:
- Defendant: {fields.get('defendant_name', '[UNKNOWN]')}
- Arresting agency: {fields.get('arresting_agency', '[UNKNOWN]')}
- Date of arrest: {fields.get('arrest_date', '[UNKNOWN]')}
- Date of alleged incident: {fields.get('incident_date', '[UNKNOWN]')}
- Criminal charges: {fields.get('charges', '[UNKNOWN]')}
- Court name: {fields.get('court_name', '[UNKNOWN]')}
- Case number: {fields.get('case_number', '[UNKNOWN]')}
- Bail amount: {fields.get('bail_amount', '[UNKNOWN]')}
- Defense theory: {fields.get('defense_theory', '[UNKNOWN]')}
- Prior criminal record: {fields.get('prior_record', '[UNKNOWN]')}
- Defense witnesses: {fields.get('witness_names', '[UNKNOWN]')}
- Exculpatory evidence: {fields.get('evidence_description', '[UNKNOWN]')}

The motion must include:
1. Caption (court, defendant, case number)
2. INTRODUCTION summarizing the motion and relief requested
3. STATEMENT OF FACTS with numbered paragraphs covering:
   - The arrest: date, agency, circumstances
   - The charges filed against defendant
   - The defense's version of events
   - Exculpatory evidence available
4. LEGAL ARGUMENT section:
   - Standard for dismissal / demurrer
   - Why the charges fail to state an offense OR lack probable cause
   - Constitutional arguments (Fourth Amendment if unlawful search/seizure,
     Fifth Amendment if self-incrimination, Sixth Amendment if right to counsel violated)
   - Application of defense theory to the facts
5. CONCLUSION requesting dismissal of all charges with prejudice
6. Signature block with [ATTORNEY NAME], [BAR NUMBER], [FIRM NAME], [ADDRESS], [DATE]"""


def _build_employment_dispute_prompt(fields: dict) -> str:
    return f"""Draft a formal civil complaint for an employment dispute case.

CASE INFORMATION:
- Employee (Plaintiff): {fields.get('plaintiff_name', '[UNKNOWN]')}
- Employer (Defendant): {fields.get('defendant_name', '[UNKNOWN]')}
- Employment start date: {fields.get('employment_start_date', '[UNKNOWN]')}
- Termination / incident date: {fields.get('termination_date', '[UNKNOWN]')}
- Job title: {fields.get('job_title', '[UNKNOWN]')}
- Type of dispute: {fields.get('dispute_type', '[UNKNOWN]')}
- Description of dispute: {fields.get('dispute_description', '[UNKNOWN]')}
- HR complaint filed: {fields.get('hr_complaint_filed', '[UNKNOWN]')}
- EEOC charge filed: {fields.get('eeoc_charge_filed', '[UNKNOWN]')}
- Wages owed: {fields.get('wages_owed', '[UNKNOWN]')}
- Total damages claimed: {fields.get('damages_claimed', '[UNKNOWN]')}
- Witnesses: {fields.get('witness_names', '[UNKNOWN]')}

The complaint must include:
1. Caption (court, parties, case number as [CASE NO. TO BE ASSIGNED])
2. PARTIES section
3. JURISDICTION AND VENUE section
   - If discrimination claim: cite 42 U.S.C. § 2000e (Title VII), 29 U.S.C. § 621 (ADEA),
     or 42 U.S.C. § 12101 (ADA) as applicable
   - If wage claim: cite 29 U.S.C. § 201 (FLSA) as applicable
4. EXHAUSTION OF ADMINISTRATIVE REMEDIES (if EEOC charge was filed)
5. FACTUAL ALLEGATIONS section with numbered paragraphs covering:
   - Employment history and job title
   - Description of the conduct / events
   - Internal complaints made (HR, management)
   - Employer's response or failure to respond
   - The termination or ongoing harm
   - Economic and emotional damages
6. CAUSES OF ACTION (include all that apply based on dispute_type):
   - Wrongful Termination in Violation of Public Policy
   - Discrimination (if applicable) — protected class, adverse action, causal link
   - Hostile Work Environment (if applicable)
   - Retaliation (if applicable)
   - Breach of Employment Contract (if applicable)
   - Wage and Hour Violations (if applicable) — cite FLSA
7. PRAYER FOR RELIEF requesting:
   - Back pay and front pay
   - Compensatory damages (emotional distress)
   - Punitive damages (for intentional conduct)
   - Reinstatement or injunctive relief
   - Attorney's fees under applicable fee-shifting statutes
   - Costs of suit
8. JURY DEMAND
9. Signature block with [ATTORNEY NAME], [BAR NUMBER], [FIRM NAME], [ADDRESS], [DATE]"""


# ──────────────────────────────────────────────
# Prompt dispatcher
# ──────────────────────────────────────────────
_PROMPT_BUILDERS = {
    "personal_injury":   _build_personal_injury_prompt,
    "eminent_domain":    _build_eminent_domain_prompt,
    "contract_dispute":  _build_contract_dispute_prompt,
    "property_damage":   _build_property_damage_prompt,
    "family_law":        _build_family_law_prompt,
    "criminal_defense":  _build_criminal_defense_prompt,
    "employment_dispute": _build_employment_dispute_prompt,
}


def build_draft_prompt(case_type: str, normalized_fields: dict) -> str:
    """
    Build the user-turn prompt for Claude based on case type.
    normalized_fields must already have [UNKNOWN] substituted for missing values
    (done by normalize_case_fields() in app.py before this is called).

    Raises ValueError for unsupported case types.
    """
    builder = _PROMPT_BUILDERS.get(case_type)
    if not builder:
        raise ValueError(
            f"No complaint template for case type '{case_type}'. "
            f"Supported: {list(_PROMPT_BUILDERS.keys())}"
        )
    return builder(normalized_fields)


# ──────────────────────────────────────────────
# Claude API call
# ──────────────────────────────────────────────
def _call_claude(user_prompt: str) -> str:
    """
    Call the Anthropic Claude API and return the complaint text.

    Uses the official anthropic SDK.
    Raises RuntimeError if the API key is missing or the call fails.
    """
    if not ANTHROPIC_API_KEY or _claude_client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Set it before starting the server: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    try:
        message = _claude_client.messages.create(
            model      = CLAUDE_MODEL,
            max_tokens = CLAUDE_MAX_TOKENS,
            system     = _system_prompt(),
            messages   = [
                {"role": "user", "content": user_prompt}
            ],
        )

        # Extract text from the first content block
        complaint_text = ""
        for block in message.content:
            if block.type == "text":
                complaint_text += block.text

        if not complaint_text.strip():
            raise RuntimeError("Claude returned an empty response.")

        # ── Token cost logging ────────────────────────────────────────────────
        # claude-sonnet-4-6 pricing: $3 / 1M input, $15 / 1M output
        # Logged so you can track spend per draft call in app.log.
        tokens_in    = message.usage.input_tokens
        tokens_out   = message.usage.output_tokens
        cost_usd     = (tokens_in / 1_000_000 * 3.0) + (tokens_out / 1_000_000 * 15.0)
        logger.info(
            f"Claude API | model={CLAUDE_MODEL} | "
            f"tokens_in={tokens_in} | tokens_out={tokens_out} | "
            f"est_cost=${cost_usd:.4f}"
        )

        return complaint_text.strip()

    except anthropic.APITimeoutError:
        # Return a recoverable placeholder so the attorney can retry without losing
        # their session. The router will save this text and unset draft_generated so
        # the next call attempts a fresh API call rather than serving the placeholder.
        logger.warning(
            f"Claude API timed out after {CLAUDE_TIMEOUT_SEC}s — returning placeholder draft."
        )
        return (
            "[COMPLAINT GENERATION TIMED OUT]\n\n"
            "The complaint could not be generated within the allowed time. "
            "Please use POST /draft/{case_id} to retry. "
            "All case information has been preserved."
        )
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"Failed to connect to Anthropic API: {e}") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError(f"Anthropic API rate limit exceeded: {e}") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(
            f"Anthropic API error {e.status_code}: {e.message}"
        ) from e


# ──────────────────────────────────────────────
# Draft result helpers
# ──────────────────────────────────────────────
def _count_unknowns(text: str) -> int:
    """Count how many [UNKNOWN] placeholders remain in the draft."""
    return len(re.findall(r'\[UNKNOWN\]', text))


def _word_count(text: str) -> int:
    return len(text.split())


def _save_draft(case_id: str, draft_text: str) -> None:
    """Persist the generated complaint and set draft_generated = 1."""
    now = datetime.utcnow().isoformat()
    with _get_db() as conn:
        conn.execute(
            """
            UPDATE case_sessions
            SET draft_generated = 1,
                draft_text      = ?,
                updated_at      = ?
            WHERE case_id = ?
            """,
            (draft_text, now, case_id),
        )
    logger.info(f"Draft saved | case_id={case_id} | words={_word_count(draft_text)}")


# ══════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════

def draft_complaint(
    case_id:          str,
    provided_fields:  dict,
    required_fields:  list[str],
    case_type:        str,
    force_draft:      bool = False,
    can_draft:        bool = True,
    draft_generated:  bool = False,
    draft_text:       str | None = None,
) -> dict:
    """
    Generate a formal legal complaint for the given case session.

    Called by the /draft/{case_id} endpoint in complaint_router.py after
    it loads the case session via get_case_session() from app.py.

    Draft lock:
        If draft_generated is True and draft_text is not None, the cached
        draft is returned immediately without calling Claude. This prevents
        duplicate API charges on page reloads or button double-clicks.

    Validation gate:
        If can_draft is False (validation failed and force_draft is not set),
        raises ValueError. The router converts this to a 422 response with
        the validation issues so the attorney knows what to fix.

    Args:
        case_id         : UUID of the case session
        provided_fields : Collected case fields (from get_case_session)
        required_fields : Required element IDs for this case type
        case_type       : Detected case type string
        force_draft     : True if attorney chose to proceed with missing fields
        can_draft       : From the latest validation result (is_valid OR force_draft)
        draft_generated : True if a draft already exists in the DB
        draft_text      : The existing draft text (if draft_generated is True)

    Returns:
        {
            "case_id":       str,
            "case_type":     str,
            "complaint":     str,    # full complaint text
            "from_cache":    bool,   # True if returned from DB cache
            "word_count":    int,
            "unknown_count": int     # number of [UNKNOWN] placeholders remaining
        }

    Raises:
        ValueError  — if can_draft is False (caller should return 422)
        RuntimeError — if Claude API call fails (caller should return 503)
    """
    # ── Draft lock: return cached draft if already generated ─────────────────
    if draft_generated and draft_text:
        logger.info(f"Draft cache hit | case_id={case_id}")
        return {
            "case_id":       case_id,
            "case_type":     case_type,
            "complaint":     draft_text,
            "from_cache":    True,
            "word_count":    _word_count(draft_text),
            "unknown_count": _count_unknowns(draft_text),
        }

    # ── Validation gate ───────────────────────────────────────────────────────
    if not can_draft:
        raise ValueError(
            "Case validation failed. Required fields are missing or contain errors. "
            "Complete the intake form or use PATCH /intake/{case_id}/force to proceed anyway."
        )

    # ── Normalize fields: replace missing required with [UNKNOWN] ─────────────
    normalized = normalize_case_fields(provided_fields, required_fields)

    # ── Build court caption from intake fields (or auto-detect from case type) ─
    caption = build_court_caption(normalized, case_type)

    req_id = str(uuid.uuid4())[:8]
    logger.info(
        f"[{req_id}] Drafting | case_id={case_id} | case_type={case_type}"
    )
    logger.debug(f"[{req_id}] Caption:\n{caption}")

    case_prompt = build_draft_prompt(case_type, normalized)
    user_prompt = (
        f"COURT CAPTION\n-------------\n{caption}\n\n"
        f"COMPLAINT BODY\n--------------\n{case_prompt}"
    )
    complaint_text = _call_claude(user_prompt)

    # ── Persist draft and set lock ────────────────────────────────────────────
    _save_draft(case_id, complaint_text)

    unknown_count = _count_unknowns(complaint_text)
    word_count    = _word_count(complaint_text)

    logger.info(
        f"[{req_id}] Draft complete | case_id={case_id} | "
        f"words={word_count} | [UNKNOWN] remaining={unknown_count}"
    )

    return {
        "case_id":       case_id,
        "case_type":     case_type,
        "complaint":     complaint_text,
        "from_cache":    False,
        "word_count":    word_count,
        "unknown_count": unknown_count,
    }