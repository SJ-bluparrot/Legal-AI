"""
classifier.py — Legal Case Type Definitions
---------------------------------------------
Defines supported case types and their descriptions.
Classification is handled by Claude Haiku via _haiku_extract() in app.py.
These constants are kept as a reference and for validation use.
"""

# ──────────────────────────────────────────────
# Allowed case types
# ──────────────────────────────────────────────
ALLOWED_CASE_TYPES = [
    "eminent_domain",
    "contract_dispute",
    "personal_injury",
    "property_damage",
    "family_law",
    "criminal_defense",
    "employment_dispute",
    "other",
]

# ──────────────────────────────────────────────
# Case type descriptions (used as reference for prompt engineering)
# ──────────────────────────────────────────────
CASE_TYPE_DESCRIPTIONS = {
    "eminent_domain": (
        "Government taking or restricting private property for public use. "
        "Examples: city seized land, government took property for a highway, state condemned a building."
    ),
    "contract_dispute": (
        "Disagreements over contracts or agreements between parties. "
        "Examples: other party broke the agreement, not paid per contract, vendor did not deliver."
    ),
    "personal_injury": (
        "Physical injuries caused by another person's negligence. "
        "Examples: injured in a car accident, slipped and fell at a store, dog bite, hit by a car."
    ),
    "property_damage": (
        "Theft, vandalism, or damage to the user's own property. "
        "Examples: phone stolen, car vandalized, house broken into, laptop stolen."
    ),
    "family_law": (
        "Divorce, child custody, adoption, or family matters. "
        "Examples: divorce, custody of a child, adoption."
    ),
    "criminal_defense": (
        "ONLY when the user themselves is accused of or charged with a crime. "
        "Examples: arrested, charged with theft, being prosecuted, court date for a DUI."
    ),
    "employment_dispute": (
        "Wrongful termination, workplace harassment, wage disputes, or unfair treatment by an employer. "
        "Examples: employer fired unfairly, boss is harassing, not paid wages, discriminated against at work."
    ),
    "other": "Any legal matter that does not clearly fit the above categories.",
}

CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.40
