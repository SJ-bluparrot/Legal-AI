"""
element_extractor.py — Required Legal Elements Per Case Type
-------------------------------------------------------------
Day 4 deliverable (updated Day 5).

Responsibility:
    Given a detected case type, return the full list of required legal
    elements needed to draft a complaint, organised into named sections.

Why sections matter:
    Every legal complaint shares a common skeleton:
        Parties       — who is suing whom
        Incident      — when and where it happened
        [Case Core]   — the facts specific to THIS type of case
        Damages       — what compensation is sought
        Supporting    — evidence, witnesses, insurance, police reports

    Grouping fields by section makes the UI feel structured and intelligent
    instead of showing a flat wall of form fields.

Architecture:
    Primary path  — Static schema (zero latency, no GPU, always reliable).
    Optional path — model extraction for unknown case types only
                    (falls back to empty list when case type is unrecognised).

Output contract:
    {
        "case_type": "personal_injury",
        "elements":  [ { id, label, description, required, section }, ... ],
        "sections":  {
            "Parties":            ["plaintiff_name", "defendant_name"],
            "Incident Details":   ["incident_date", "incident_location"],
            "Injury Details":     ["injury_description", "negligence_act", ...],
            "Damages":            ["medical_expenses", "lost_wages", "damages_claimed"],
            "Supporting Info":    ["witness_names", "insurance_info"]
        },
        "source":    "static" | "model" | "empty"
    }

    "elements" is the flat list (backwards compatible with existing callers).
    "sections" is an ordered dict: section_name → [field_id, ...] in display order.
    A field_id appears in exactly one section.

Usage:
    from element_extractor import extract_elements

    result   = extract_elements("personal_injury", model, tokenizer)
    elements = result["elements"]   # flat list — use for DB storage / validation
    sections = result["sections"]   # grouped dict — use for UI rendering
    source   = result["source"]     # "static" | "model" | "empty"
"""

import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Static Element Schemas
# ──────────────────────────────────────────────
# Each element dict:
#   id          — snake_case identifier (used as DB key and JSON key)
#   label       — human-readable form label
#   description — tooltip / help text shown next to the field
#   required    — True = must be provided before drafting; False = optional but helpful
#   section     — which UI section this field belongs to
#
# Sections used across all case types:
#   "Parties"          — plaintiff, defendant
#   "Incident Details" — date, location (always case-agnostic)
#   "[Case] Details"   — the facts specific to this case type (name varies per type)
#   "Damages"          — financial claims
#   "Supporting Info"  — witnesses, police reports, insurance, evidence
# ──────────────────────────────────────────────

STATIC_ELEMENTS: dict[str, list[dict]] = {

    # ── Personal Injury ────────────────────────────────────────────────────
    "personal_injury": [
        # Parties
        {"id": "plaintiff_name",     "label": "Plaintiff Name",           "description": "Full legal name of the injured party.",                           "required": True,  "section": "Parties"},
        {"id": "defendant_name",     "label": "Defendant Name",           "description": "Full legal name of the party responsible for the injury.",        "required": True,  "section": "Parties"},
        # Incident Details
        {"id": "incident_date",      "label": "Date of Incident",         "description": "Date when the injury occurred.",                                  "required": True,  "section": "Incident Details"},
        {"id": "incident_location",  "label": "Location of Incident",     "description": "Address or place where the incident occurred.",                   "required": True,  "section": "Incident Details"},
        # Injury Details
        {"id": "injury_description", "label": "Description of Injury",    "description": "Nature and extent of physical injuries sustained.",               "required": True,  "section": "Injury Details"},
        {"id": "negligence_act",     "label": "Negligent Act or Omission","description": "The specific act or failure to act that caused the injury.",      "required": True,  "section": "Injury Details"},
        {"id": "medical_treatment",  "label": "Medical Treatment",        "description": "Medical care received as a result of the injury.",                "required": False, "section": "Injury Details"},
        # Damages
        {"id": "medical_expenses",   "label": "Medical Expenses",         "description": "Total or estimated medical costs incurred.",                      "required": False, "section": "Damages"},
        {"id": "lost_wages",         "label": "Lost Wages",               "description": "Income lost due to inability to work after the injury.",          "required": False, "section": "Damages"},
        {"id": "damages_claimed",    "label": "Total Damages Claimed",    "description": "Total compensation amount being sought.",                         "required": False, "section": "Damages"},
        # Supporting Info
        {"id": "witness_names",      "label": "Witness Names",            "description": "Names of any witnesses to the incident.",                         "required": False, "section": "Supporting Info"},
        {"id": "insurance_info",     "label": "Insurance Information",    "description": "Relevant insurance policy details for either party.",             "required": False, "section": "Supporting Info"},
    ],

    # ── Eminent Domain ─────────────────────────────────────────────────────
    "eminent_domain": [
        # Parties
        {"id": "plaintiff_name",        "label": "Property Owner Name",    "description": "Full legal name of the property owner.",                          "required": True,  "section": "Parties"},
        {"id": "defendant_name",        "label": "Government Entity",      "description": "Name of the government body that took or restricted the property.","required": True,  "section": "Parties"},
        # Incident Details
        {"id": "taking_date",           "label": "Date of Taking",         "description": "Date when the government took or restricted the property.",       "required": True,  "section": "Incident Details"},
        {"id": "property_address",      "label": "Property Address",       "description": "Full address or legal description of the taken property.",        "required": True,  "section": "Incident Details"},
        # Taking Details
        {"id": "public_use_stated",     "label": "Stated Public Use",      "description": "The government's stated justification for taking the property.",  "required": True,  "section": "Taking Details"},
        {"id": "property_use",          "label": "Current Property Use",   "description": "How the property was being used before the taking.",              "required": False, "section": "Taking Details"},
        {"id": "compensation_offered",  "label": "Compensation Offered",   "description": "Amount the government offered as just compensation.",             "required": True,  "section": "Taking Details"},
        {"id": "fair_market_value",     "label": "Fair Market Value",      "description": "Owner's estimate or appraisal of the property's fair market value.","required": False,"section": "Taking Details"},
        # Damages
        {"id": "damages_claimed",       "label": "Additional Damages",     "description": "Any losses beyond the property value (relocation, business loss).","required": False,"section": "Damages"},
        # Supporting Info
        {"id": "appraisal_report",      "label": "Appraisal Report",       "description": "Whether an independent appraisal has been obtained.",             "required": False, "section": "Supporting Info"},
    ],

    # ── Contract Dispute ───────────────────────────────────────────────────
    "contract_dispute": [
        # Parties
        {"id": "plaintiff_name",           "label": "Plaintiff Name",            "description": "Full legal name of the party bringing the claim.",                 "required": True,  "section": "Parties"},
        {"id": "defendant_name",           "label": "Defendant Name",            "description": "Full legal name of the party in breach.",                          "required": True,  "section": "Parties"},
        # Incident Details
        {"id": "contract_date",            "label": "Contract Date",             "description": "Date the contract was signed or agreed upon.",                     "required": True,  "section": "Incident Details"},
        # Contract Details
        {"id": "contract_description",     "label": "Contract Description",      "description": "Brief description of what the contract covered.",                  "required": True,  "section": "Contract Details"},
        {"id": "written_contract",         "label": "Written Contract Exists",   "description": "Whether a written contract exists and can be produced.",           "required": False, "section": "Contract Details"},
        {"id": "performance_by_plaintiff", "label": "Plaintiff's Performance",   "description": "What the plaintiff did to fulfill their contractual obligations.", "required": True,  "section": "Contract Details"},
        {"id": "breach_description",       "label": "Description of Breach",     "description": "How and when the other party violated the contract.",              "required": True,  "section": "Contract Details"},
        {"id": "demand_letter_sent",       "label": "Demand Letter Sent",        "description": "Whether a formal demand to cure the breach was sent.",             "required": False, "section": "Contract Details"},
        # Damages
        {"id": "contract_value",           "label": "Contract Value",            "description": "Total monetary value of the original contract.",                   "required": False, "section": "Damages"},
        {"id": "damages_claimed",          "label": "Damages Claimed",           "description": "Financial loss resulting directly from the breach.",               "required": True,  "section": "Damages"},
        # Supporting Info
        {"id": "witness_names",            "label": "Witness Names",             "description": "Names of witnesses to the contract formation or its breach.",      "required": False, "section": "Supporting Info"},
    ],

    # ── Property Damage ────────────────────────────────────────────────────
    "property_damage": [
        # Parties
        {"id": "plaintiff_name",       "label": "Plaintiff Name",          "description": "Full legal name of the property owner.",                           "required": True,  "section": "Parties"},
        {"id": "defendant_name",       "label": "Defendant Name",          "description": "Full legal name of the person who caused the damage or theft.",    "required": True,  "section": "Parties"},
        # Incident Details
        {"id": "incident_date",        "label": "Date of Incident",        "description": "Date when the damage or theft occurred.",                          "required": True,  "section": "Incident Details"},
        {"id": "incident_location",    "label": "Location of Incident",    "description": "Where the damage or theft took place.",                            "required": True,  "section": "Incident Details"},
        # Damage Details
        {"id": "property_description", "label": "Property Description",    "description": "Description of the property that was damaged or stolen.",          "required": True,  "section": "Damage Details"},
        {"id": "damage_description",   "label": "Description of Damage",   "description": "Nature and extent of the damage, or details of the theft.",        "required": True,  "section": "Damage Details"},
        {"id": "property_value",       "label": "Property Value",          "description": "Estimated value of the damaged or stolen property.",               "required": True,  "section": "Damage Details"},
        {"id": "repair_cost",          "label": "Repair or Replacement Cost","description":"Cost to repair or replace the damaged property.",                  "required": False, "section": "Damage Details"},
        # Damages
        {"id": "damages_claimed",      "label": "Total Damages Claimed",   "description": "Total compensation being sought.",                                 "required": False, "section": "Damages"},
        # Supporting Info
        {"id": "police_report_number", "label": "Police Report Number",    "description": "Case number from any police report filed.",                        "required": False, "section": "Supporting Info"},
        {"id": "insurance_claim",      "label": "Insurance Claim Status",  "description": "Whether an insurance claim was filed and its outcome.",            "required": False, "section": "Supporting Info"},
        {"id": "witness_names",        "label": "Witness Names",           "description": "Names of any witnesses to the incident.",                          "required": False, "section": "Supporting Info"},
    ],

    # ── Family Law ─────────────────────────────────────────────────────────
    "family_law": [
        # Parties
        {"id": "plaintiff_name",      "label": "Petitioner Name",            "description": "Full legal name of the party filing the petition.",                "required": True,  "section": "Parties"},
        {"id": "defendant_name",      "label": "Respondent Name",            "description": "Full legal name of the other party.",                             "required": True,  "section": "Parties"},
        # Incident Details
        {"id": "marriage_date",       "label": "Date of Marriage",           "description": "Date the parties were legally married.",                           "required": True,  "section": "Incident Details"},
        {"id": "separation_date",     "label": "Date of Separation",         "description": "Date the parties physically separated.",                           "required": False, "section": "Incident Details"},
        {"id": "jurisdiction_state",  "label": "State of Jurisdiction",      "description": "US state where the petition is being filed.",                      "required": True,  "section": "Incident Details"},
        # Case Details
        {"id": "grounds_for_divorce", "label": "Grounds for Divorce",        "description": "Legal grounds cited (e.g. irreconcilable differences).",          "required": True,  "section": "Case Details"},
        {"id": "children_names",      "label": "Children's Names & Ages",    "description": "Full names and ages of any minor children of the marriage.",       "required": False, "section": "Case Details"},
        {"id": "custody_arrangement", "label": "Custody Arrangement Sought", "description": "Desired custody and visitation arrangement.",                      "required": False, "section": "Case Details"},
        {"id": "spousal_support",     "label": "Spousal Support Request",    "description": "Whether spousal support or alimony is being requested.",           "required": False, "section": "Case Details"},
        # Damages
        {"id": "property_list",       "label": "Marital Property",           "description": "List of significant marital assets to be divided.",                "required": False, "section": "Damages"},
        {"id": "debt_list",           "label": "Marital Debts",              "description": "List of significant marital debts to be divided.",                 "required": False, "section": "Damages"},
    ],

    # ── Criminal Defense ───────────────────────────────────────────────────
    "criminal_defense": [
        # Parties
        {"id": "defendant_name",       "label": "Defendant Name",           "description": "Full legal name of the accused.",                                  "required": True,  "section": "Parties"},
        {"id": "arresting_agency",     "label": "Arresting Agency",         "description": "Law enforcement agency that made the arrest.",                     "required": True,  "section": "Parties"},
        # Incident Details
        {"id": "arrest_date",          "label": "Date of Arrest",           "description": "Date the defendant was arrested.",                                 "required": True,  "section": "Incident Details"},
        {"id": "incident_date",        "label": "Date of Alleged Incident", "description": "Date the alleged offense occurred.",                               "required": True,  "section": "Incident Details"},
        # Case Details
        {"id": "charges",              "label": "Criminal Charges",         "description": "Specific charges filed against the defendant.",                    "required": True,  "section": "Case Details"},
        {"id": "court_name",           "label": "Court Name",               "description": "Name of the court where the case is being heard.",                 "required": False, "section": "Case Details"},
        {"id": "case_number",          "label": "Case Number",              "description": "Court-assigned case number.",                                      "required": False, "section": "Case Details"},
        {"id": "bail_amount",          "label": "Bail Amount",              "description": "Bail set by the court, if applicable.",                           "required": False, "section": "Case Details"},
        {"id": "defense_theory",       "label": "Defense Theory",           "description": "Brief description of the planned defense strategy.",               "required": False, "section": "Case Details"},
        # Supporting Info
        {"id": "prior_record",         "label": "Prior Criminal Record",    "description": "Any prior convictions relevant to the defense.",                   "required": False, "section": "Supporting Info"},
        {"id": "witness_names",        "label": "Defense Witnesses",        "description": "Names of witnesses who can support the defense.",                  "required": False, "section": "Supporting Info"},
        {"id": "evidence_description", "label": "Exculpatory Evidence",     "description": "Any evidence that supports the defendant's innocence.",            "required": False, "section": "Supporting Info"},
    ],

    # ── Employment Dispute ─────────────────────────────────────────────────
    "employment_dispute": [
        # Parties
        {"id": "plaintiff_name",        "label": "Employee Name",            "description": "Full legal name of the employee.",                                 "required": True,  "section": "Parties"},
        {"id": "defendant_name",        "label": "Employer Name",            "description": "Full legal name of the employer or company.",                      "required": True,  "section": "Parties"},
        # Incident Details
        {"id": "employment_start_date", "label": "Employment Start Date",    "description": "Date the employee began working for this employer.",               "required": True,  "section": "Incident Details"},
        {"id": "termination_date",      "label": "Termination / Incident Date","description":"Date of termination, last harassment incident, or wage violation.","required": True,  "section": "Incident Details"},
        {"id": "job_title",             "label": "Job Title",                "description": "Employee's job title or position held.",                           "required": True,  "section": "Incident Details"},
        # Case Details
        {"id": "dispute_type",          "label": "Type of Dispute",          "description": "E.g. wrongful termination, harassment, wage theft, discrimination.","required": True,  "section": "Case Details"},
        {"id": "dispute_description",   "label": "Description of Dispute",   "description": "Detailed account of what happened and relevant conduct.",          "required": True,  "section": "Case Details"},
        {"id": "hr_complaint_filed",    "label": "HR Complaint Filed",       "description": "Whether a complaint was made internally to HR and the outcome.",   "required": False, "section": "Case Details"},
        {"id": "eeoc_charge_filed",     "label": "EEOC Charge Filed",        "description": "Whether an EEOC charge was filed (required for discrimination claims).","required": False,"section": "Case Details"},
        # Damages
        {"id": "wages_owed",            "label": "Wages Owed",               "description": "Amount of unpaid wages, overtime, or benefits owed.",              "required": False, "section": "Damages"},
        {"id": "damages_claimed",       "label": "Total Damages Claimed",    "description": "Total compensation being sought.",                                 "required": False, "section": "Damages"},
        # Supporting Info
        {"id": "witness_names",         "label": "Witness Names",            "description": "Names of coworkers or others who witnessed the conduct.",          "required": False, "section": "Supporting Info"},
    ],

    "other": [],
}


# ──────────────────────────────────────────────
# Section Builder
# ──────────────────────────────────────────────
def _build_sections(elements: list[dict]) -> dict:
    """
    Build an ordered section → [field_id, ...] mapping from a flat element list.

    Preserves the natural ordering of sections as they appear in the schema
    (Parties always first, Supporting Info always last) by using an OrderedDict
    and inserting sections in first-encountered order.

    Returns:
        OrderedDict of { section_name: [field_id, ...] }
        e.g. {"Parties": ["plaintiff_name", "defendant_name"],
               "Incident Details": ["incident_date", "incident_location"], ...}
    """
    sections: dict[str, list[str]] = OrderedDict()
    for el in elements:
        sec = el.get("section", "Other")
        if sec not in sections:
            sections[sec] = []
        sections[sec].append(el["id"])
    return sections


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def extract_elements(
    case_type: str,
    model=None,
    tokenizer=None,
    use_model: bool = False,
) -> dict:
    """
    Return required legal elements for a given case type, grouped into sections.
    Uses the static schema only — model/tokenizer params are ignored.

    Args:
        case_type  : Detected case type string
        model      : Ignored (kept for API compatibility)
        tokenizer  : Ignored (kept for API compatibility)
        use_model  : Ignored (kept for API compatibility)

    Returns:
        {
            "case_type": str,
            "elements":  list[dict],
            "sections":  dict,
            "source":    "static" | "empty"
        }
    """
    # ── Static schema (covers all known case types) ──────────────────────────
    if case_type in STATIC_ELEMENTS and case_type != "other":
        elements = STATIC_ELEMENTS[case_type]
        sections = _build_sections(elements)
        logger.info(
            f"extract_elements: static | case='{case_type}' | "
            f"{len(elements)} fields across {len(sections)} sections"
        )
        return {
            "case_type": case_type,
            "elements":  elements,
            "sections":  sections,
            "source":    "static",
        }

    # ── Empty fallback for "other" or unrecognised case types ───────────────
    logger.info(f"extract_elements: empty fallback for case_type='{case_type}'")
    return {
        "case_type": case_type,
        "elements":  [],
        "sections":  {},
        "source":    "empty",
    }