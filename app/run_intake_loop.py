import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple

from ollama import chat

# Allow running from app/ directory or project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from assemble_contract import load_questionnaire

CONFIG_DIR = ROOT / "config"

MODEL = os.getenv("EXTRACTION_MODEL", "qwen3:4b")
DEFAULT_CONTRACT_TYPE = "NDA"


# ---------------------------------------------------------------------------
# Schema helpers (contract-type agnostic — driven entirely by config)
# ---------------------------------------------------------------------------
def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# Cache questionnaires per contract type
_questionnaire_cache: Dict[str, dict] = {}


def get_questionnaire(contract_type: str) -> dict:
    if contract_type not in _questionnaire_cache:
        _questionnaire_cache[contract_type] = load_questionnaire(contract_type)
    return _questionnaire_cache[contract_type]


def schema_fields(contract_type: str = DEFAULT_CONTRACT_TYPE) -> List[dict]:
    return get_questionnaire(contract_type)["fields"]


def field_name(field: dict) -> str:
    return field.get("name") or field.get("key")


def required_fields(contract_type: str = DEFAULT_CONTRACT_TYPE) -> List[str]:
    return [field_name(f) for f in schema_fields(contract_type) if f.get("required")]


def field_lookup(contract_type: str = DEFAULT_CONTRACT_TYPE) -> Dict[str, dict]:
    return {field_name(f): f for f in schema_fields(contract_type)}


# ---------------------------------------------------------------------------
# Extraction JSON schema (for Ollama structured output)
# ---------------------------------------------------------------------------
# Built dynamically from the questionnaire so it works for any contract type.
def build_extraction_schema(contract_type: str = DEFAULT_CONTRACT_TYPE) -> dict:
    props = {}
    for f in schema_fields(contract_type):
        props[field_name(f)] = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "known_answers": {
                "type": "object",
                "properties": props,
            },
            "field_evidence": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "follow_up_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "question": {"type": "string"},
                    },
                    "required": ["field", "question"],
                },
            },
        },
        "required": ["known_answers", "field_evidence", "follow_up_questions"],
    }


# ---------------------------------------------------------------------------
# Few-shot examples (per contract type)
#
# Each contract type has 3 examples covering: direct, partial, conversational.
# The FORMAT is contract-agnostic; only the content is type-specific.
# ---------------------------------------------------------------------------
NDA_FEW_SHOT_EXAMPLES = """
=== EXAMPLE 1: Fully specified direct prompt ===
User request: "We need a unilateral NDA between Acme Corp., a Delaware corporation, and Beta Ventures LLC, a Connecticut LLC, to evaluate a potential partnership for 3 years under Connecticut law using litigation. Acme's email is legal@acme.com and Beta's email is ops@betaventures.com."

Correct extraction:
{
  "known_answers": {
    "nda_type": "Unilateral",
    "party_a_name": "Acme Corp.",
    "party_a_entity_details": "Delaware corporation",
    "party_a_email": "legal@acme.com",
    "party_b_name": "Beta Ventures LLC",
    "party_b_entity_details": "Connecticut LLC",
    "party_b_email": "ops@betaventures.com",
    "purpose": "evaluate a potential partnership",
    "confidentiality_period_number": "3",
    "confidentiality_period_unit": "years",
    "governing_law": "Connecticut",
    "dispute_resolution_method": "Litigation"
  },
  "field_evidence": {
    "nda_type": "unilateral NDA",
    "party_a_name": "Acme Corp.",
    "party_a_entity_details": "a Delaware corporation",
    "party_a_email": "Acme's email is legal@acme.com",
    "party_b_name": "Beta Ventures LLC",
    "party_b_entity_details": "a Connecticut LLC",
    "party_b_email": "Beta's email is ops@betaventures.com",
    "purpose": "to evaluate a potential partnership",
    "confidentiality_period_number": "for 3 years",
    "confidentiality_period_unit": "for 3 years",
    "governing_law": "under Connecticut law",
    "dispute_resolution_method": "using litigation"
  },
  "follow_up_questions": []
}

=== EXAMPLE 2: Partially specified prompt (missing entity details and emails) ===
User request: "We need a unilateral NDA between Acme Corp and Beta Ventures LLC to evaluate a potential partnership for 3 years under Connecticut law using litigation."

Correct extraction:
{
  "known_answers": {
    "nda_type": "Unilateral",
    "party_a_name": "Acme Corp",
    "party_b_name": "Beta Ventures LLC",
    "purpose": "evaluate a potential partnership",
    "confidentiality_period_number": "3",
    "confidentiality_period_unit": "years",
    "governing_law": "Connecticut",
    "dispute_resolution_method": "Litigation"
  },
  "field_evidence": {
    "nda_type": "unilateral NDA",
    "party_a_name": "Acme Corp",
    "party_b_name": "Beta Ventures LLC",
    "purpose": "to evaluate a potential partnership",
    "confidentiality_period_number": "for 3 years",
    "confidentiality_period_unit": "for 3 years",
    "governing_law": "under Connecticut law",
    "dispute_resolution_method": "using litigation"
  },
  "follow_up_questions": [
    {"field": "party_a_entity_details", "question": "What are the entity details for Acme Corp (e.g., Delaware corporation)?"},
    {"field": "party_a_email", "question": "What is the email address for Acme Corp?"},
    {"field": "party_b_entity_details", "question": "What are the entity details for Beta Ventures LLC (e.g., Connecticut LLC)?"},
    {"field": "party_b_email", "question": "What is the email address for Beta Ventures LLC?"}
  ]
}

=== EXAMPLE 3: Conversational / indirect prompt ===
User request: "Acme Corp and Beta Ventures LLC are exploring a possible partnership and need an NDA. It should last 3 years, follow Connecticut law, and use litigation if there's a dispute. Acme's email is legal@acme.com and Beta's email is ops@betaventures.com."

Correct extraction:
{
  "known_answers": {
    "party_a_name": "Acme Corp",
    "party_a_email": "legal@acme.com",
    "party_b_name": "Beta Ventures LLC",
    "party_b_email": "ops@betaventures.com",
    "purpose": "exploring a possible partnership",
    "confidentiality_period_number": "3",
    "confidentiality_period_unit": "years",
    "governing_law": "Connecticut",
    "dispute_resolution_method": "Litigation"
  },
  "field_evidence": {
    "party_a_name": "Acme Corp",
    "party_a_email": "Acme's email is legal@acme.com",
    "party_b_name": "Beta Ventures LLC",
    "party_b_email": "Beta's email is ops@betaventures.com",
    "purpose": "exploring a possible partnership",
    "confidentiality_period_number": "last 3 years",
    "confidentiality_period_unit": "last 3 years",
    "governing_law": "follow Connecticut law",
    "dispute_resolution_method": "use litigation if there's a dispute"
  },
  "follow_up_questions": [
    {"field": "nda_type", "question": "Should this be a Mutual or Unilateral NDA?"},
    {"field": "party_a_entity_details", "question": "What are the entity details for Acme Corp (e.g., Delaware corporation)?"},
    {"field": "party_b_entity_details", "question": "What are the entity details for Beta Ventures LLC (e.g., Connecticut LLC)?"}
  ]
}
"""

CONSULTING_FEW_SHOT_EXAMPLES = """
=== EXAMPLE 1: Fully specified direct prompt ===
User request: "We need a consulting agreement between Acme Corp at 123 Main St, Hartford CT 06103 (legal@acme.com) and Jane Smith Consulting at 456 Oak Ave, New Haven CT 06510 (jane@smithconsulting.com). Jane will provide marketing strategy and brand positioning services for $25,000, paid net 30. The agreement starts January 15, 2026 and ends June 30, 2026. Either party can terminate with 30 days notice. Connecticut law, arbitration for disputes. Client owns all IP."

Correct extraction:
{
  "known_answers": {
    "consulting_type": "Standard",
    "client_name": "Acme Corp",
    "client_address": "123 Main St, Hartford CT 06103",
    "client_email": "legal@acme.com",
    "consultant_name": "Jane Smith Consulting",
    "consultant_address": "456 Oak Ave, New Haven CT 06510",
    "consultant_email": "jane@smithconsulting.com",
    "effective_date": "January 15, 2026",
    "services_description": "marketing strategy and brand positioning services",
    "compensation_amount": "$25,000",
    "payment_schedule": "Net 30",
    "term_end_date": "June 30, 2026",
    "termination_notice_days": "30",
    "governing_law": "Connecticut",
    "ip_ownership": "Client",
    "dispute_resolution_method": "Arbitration"
  },
  "field_evidence": {
    "consulting_type": "consulting agreement",
    "client_name": "Acme Corp",
    "client_address": "123 Main St, Hartford CT 06103",
    "client_email": "legal@acme.com",
    "consultant_name": "Jane Smith Consulting",
    "consultant_address": "456 Oak Ave, New Haven CT 06510",
    "consultant_email": "jane@smithconsulting.com",
    "effective_date": "starts January 15, 2026",
    "services_description": "marketing strategy and brand positioning services",
    "compensation_amount": "$25,000",
    "payment_schedule": "paid net 30",
    "term_end_date": "ends June 30, 2026",
    "termination_notice_days": "terminate with 30 days notice",
    "governing_law": "Connecticut law",
    "ip_ownership": "Client owns all IP",
    "dispute_resolution_method": "arbitration for disputes"
  },
  "follow_up_questions": []
}

=== EXAMPLE 2: Partially specified prompt ===
User request: "I need a consulting agreement. BrightWave Marketing will provide social media management and content creation for TechStart Inc. The fee is $5,000 per month, starting March 1, 2026. Use New York law. The consultant keeps the IP."

Correct extraction:
{
  "known_answers": {
    "consulting_type": "Standard",
    "client_name": "TechStart Inc",
    "consultant_name": "BrightWave Marketing",
    "effective_date": "March 1, 2026",
    "services_description": "social media management and content creation",
    "compensation_amount": "$5,000 per month",
    "payment_schedule": "Monthly",
    "governing_law": "New York",
    "ip_ownership": "Consultant"
  },
  "field_evidence": {
    "consulting_type": "consulting agreement",
    "client_name": "TechStart Inc",
    "consultant_name": "BrightWave Marketing",
    "effective_date": "starting March 1, 2026",
    "services_description": "social media management and content creation",
    "compensation_amount": "$5,000 per month",
    "payment_schedule": "$5,000 per month",
    "governing_law": "New York law",
    "ip_ownership": "consultant keeps the IP"
  },
  "follow_up_questions": [
    {"field": "client_address", "question": "What is the address for TechStart Inc?"},
    {"field": "client_email", "question": "What is the email address for TechStart Inc?"},
    {"field": "consultant_address", "question": "What is the address for BrightWave Marketing?"},
    {"field": "consultant_email", "question": "What is the email address for BrightWave Marketing?"},
    {"field": "term_end_date", "question": "When should the agreement end (specific date or 'upon completion of services')?"},
    {"field": "termination_notice_days", "question": "How many days written notice should be required to terminate the agreement?"}
  ]
}

=== EXAMPLE 3: Conversational / indirect prompt ===
User request: "We're hiring a consultant to help with our website redesign. The company is called PixelPerfect Design and they'll charge us $150 an hour. We want to use California law. My company is GlobalTech Solutions and my email is cto@globaltech.com."

Correct extraction:
{
  "known_answers": {
    "consulting_type": "Standard",
    "client_name": "GlobalTech Solutions",
    "client_email": "cto@globaltech.com",
    "consultant_name": "PixelPerfect Design",
    "services_description": "website redesign",
    "compensation_amount": "$150 per hour",
    "governing_law": "California"
  },
  "field_evidence": {
    "consulting_type": "hiring a consultant",
    "client_name": "GlobalTech Solutions",
    "client_email": "cto@globaltech.com",
    "consultant_name": "PixelPerfect Design",
    "services_description": "website redesign",
    "compensation_amount": "$150 an hour",
    "governing_law": "California law"
  },
  "follow_up_questions": [
    {"field": "client_address", "question": "What is the address for GlobalTech Solutions?"},
    {"field": "consultant_address", "question": "What is the address for PixelPerfect Design?"},
    {"field": "consultant_email", "question": "What is the email address for PixelPerfect Design?"},
    {"field": "effective_date", "question": "When should the consulting agreement take effect?"},
    {"field": "payment_schedule", "question": "Payment Schedule? (Upon Completion / Net 30 / Monthly / Milestone-Based)"},
    {"field": "term_end_date", "question": "When should the agreement end (specific date or 'upon completion of services')?"},
    {"field": "termination_notice_days", "question": "How many days written notice should be required to terminate?"},
    {"field": "ip_ownership", "question": "Who should own the intellectual property created? (Client / Consultant / Shared)"}
  ]
}
"""

EMPLOYMENT_FEW_SHOT_EXAMPLES = """
=== EXAMPLE 1: Fully specified direct prompt ===
User request: "We need an employment agreement for TechCorp Inc. at 100 Innovation Dr, San Francisco, CA 94105 hiring Jane Smith of 456 Oak Ave, San Francisco, CA 94110 as a Senior Software Engineer. Full-time, starting January 15, 2026. Salary is $120,000 per year paid bi-weekly. She'll work at our main office. 30 days notice to terminate. California law, arbitration for disputes."

Correct extraction:
{
  "known_answers": {
    "employment_type": "Standard",
    "employer_name": "TechCorp Inc.",
    "employer_address": "100 Innovation Dr, San Francisco, CA 94105",
    "employee_name": "Jane Smith",
    "employee_address": "456 Oak Ave, San Francisco, CA 94110",
    "job_title": "Senior Software Engineer",
    "start_date": "January 15, 2026",
    "employment_basis": "Full-Time",
    "compensation_amount": "$120,000 per year",
    "pay_frequency": "Bi-Weekly",
    "work_location": "100 Innovation Dr, San Francisco, CA 94105",
    "termination_notice_days": "30",
    "governing_law": "California",
    "dispute_resolution_method": "Arbitration"
  },
  "field_evidence": {
    "employment_type": "employment agreement",
    "employer_name": "TechCorp Inc.",
    "employer_address": "100 Innovation Dr, San Francisco, CA 94105",
    "employee_name": "Jane Smith",
    "employee_address": "456 Oak Ave, San Francisco, CA 94110",
    "job_title": "Senior Software Engineer",
    "start_date": "starting January 15, 2026",
    "employment_basis": "Full-time",
    "compensation_amount": "$120,000 per year",
    "pay_frequency": "paid bi-weekly",
    "work_location": "work at our main office",
    "termination_notice_days": "30 days notice to terminate",
    "governing_law": "California law",
    "dispute_resolution_method": "arbitration for disputes"
  },
  "follow_up_questions": [
    {"field": "job_duties", "question": "What are the primary job duties and responsibilities for this position?"}
  ]
}

=== EXAMPLE 2: Partially specified prompt ===
User request: "I need to hire a Marketing Manager at Bright Solutions LLC. The salary is $85,000 per year, paid semi-monthly. The job starts on March 1, 2026. We're in Texas."

Correct extraction:
{
  "known_answers": {
    "employment_type": "Standard",
    "employer_name": "Bright Solutions LLC",
    "job_title": "Marketing Manager",
    "start_date": "March 1, 2026",
    "compensation_amount": "$85,000 per year",
    "pay_frequency": "Semi-Monthly",
    "governing_law": "Texas"
  },
  "field_evidence": {
    "employment_type": "hire",
    "employer_name": "Bright Solutions LLC",
    "job_title": "Marketing Manager",
    "start_date": "starts on March 1, 2026",
    "compensation_amount": "$85,000 per year",
    "pay_frequency": "paid semi-monthly",
    "governing_law": "We're in Texas"
  },
  "follow_up_questions": [
    {"field": "employer_address", "question": "What is the full address for Bright Solutions LLC?"},
    {"field": "employee_name", "question": "What is the employee's full name?"},
    {"field": "employee_address", "question": "What is the employee's address?"},
    {"field": "job_duties", "question": "What are the primary job duties and responsibilities for this position?"},
    {"field": "employment_basis", "question": "Employment Basis? (Full-Time / Part-Time / At-Will / Fixed-Term)"},
    {"field": "work_location", "question": "What is the primary work location?"},
    {"field": "termination_notice_days", "question": "How many days written notice should be required to terminate?"}
  ]
}

=== EXAMPLE 3: Conversational / indirect prompt ===
User request: "We're bringing on a new data analyst at our company, DataFlow Analytics. They'll be working part-time, $35 an hour, paid weekly. We need this under New York law. The role starts next month."

Correct extraction:
{
  "known_answers": {
    "employment_type": "Standard",
    "employer_name": "DataFlow Analytics",
    "job_title": "Data Analyst",
    "employment_basis": "Part-Time",
    "compensation_amount": "$35 per hour",
    "pay_frequency": "Weekly",
    "governing_law": "New York"
  },
  "field_evidence": {
    "employment_type": "bringing on a new",
    "employer_name": "DataFlow Analytics",
    "job_title": "data analyst",
    "employment_basis": "working part-time",
    "compensation_amount": "$35 an hour",
    "pay_frequency": "paid weekly",
    "governing_law": "New York law"
  },
  "follow_up_questions": [
    {"field": "employer_address", "question": "What is the full address for DataFlow Analytics?"},
    {"field": "employee_name", "question": "What is the employee's full name?"},
    {"field": "employee_address", "question": "What is the employee's address?"},
    {"field": "job_duties", "question": "What are the primary job duties and responsibilities for this position?"},
    {"field": "start_date", "question": "What is the exact employment start date?"},
    {"field": "work_location", "question": "What is the primary work location?"},
    {"field": "termination_notice_days", "question": "How many days written notice should be required to terminate?"}
  ]
}
"""

SERVICE_FEW_SHOT_EXAMPLES = """
=== EXAMPLE 1: Fully specified direct prompt ===
User request: "We need a service agreement between GlobalTech Solutions at 500 Market St, San Francisco CA 94105 and CleanPro Services LLC at 200 Oak Blvd, Oakland CA 94612. CleanPro will provide commercial office cleaning including daily janitorial and weekly deep cleaning for $3,500 per month, paid monthly. Starting February 1, 2026. 30 days notice to terminate. California law, mediation for disputes. Client owns all IP."

Correct extraction:
{
  "known_answers": {
    "service_type": "Standard",
    "client_name": "GlobalTech Solutions",
    "client_address": "500 Market St, San Francisco CA 94105",
    "contractor_name": "CleanPro Services LLC",
    "contractor_address": "200 Oak Blvd, Oakland CA 94612",
    "services_description": "commercial office cleaning including daily janitorial and weekly deep cleaning",
    "effective_date": "February 1, 2026",
    "compensation_amount": "$3,500 per month",
    "payment_schedule": "Monthly",
    "termination_notice_days": "30",
    "governing_law": "California",
    "ip_ownership": "Client",
    "dispute_resolution_method": "Mediation"
  },
  "field_evidence": {
    "service_type": "service agreement",
    "client_name": "GlobalTech Solutions",
    "client_address": "500 Market St, San Francisco CA 94105",
    "contractor_name": "CleanPro Services LLC",
    "contractor_address": "200 Oak Blvd, Oakland CA 94612",
    "services_description": "commercial office cleaning including daily janitorial and weekly deep cleaning",
    "effective_date": "Starting February 1, 2026",
    "compensation_amount": "$3,500 per month",
    "payment_schedule": "paid monthly",
    "termination_notice_days": "30 days notice to terminate",
    "governing_law": "California law",
    "ip_ownership": "Client owns all IP",
    "dispute_resolution_method": "mediation for disputes"
  },
  "follow_up_questions": []
}

=== EXAMPLE 2: Partially specified prompt ===
User request: "I need a service agreement. WebWorks Design will build a new website for Summit Corp. The project costs $15,000 total, to be paid upon completion. Use Texas law. The contractor keeps their tools and methods."

Correct extraction:
{
  "known_answers": {
    "service_type": "Standard",
    "client_name": "Summit Corp",
    "contractor_name": "WebWorks Design",
    "services_description": "build a new website",
    "compensation_amount": "$15,000",
    "payment_schedule": "Upon Completion",
    "governing_law": "Texas",
    "ip_ownership": "Contractor"
  },
  "field_evidence": {
    "service_type": "service agreement",
    "client_name": "Summit Corp",
    "contractor_name": "WebWorks Design",
    "services_description": "build a new website",
    "compensation_amount": "$15,000 total",
    "payment_schedule": "paid upon completion",
    "governing_law": "Texas law",
    "ip_ownership": "contractor keeps their tools and methods"
  },
  "follow_up_questions": [
    {"field": "client_address", "question": "What is the address for Summit Corp?"},
    {"field": "contractor_address", "question": "What is the address for WebWorks Design?"},
    {"field": "effective_date", "question": "When should the service agreement take effect?"},
    {"field": "termination_notice_days", "question": "How many days written notice should be required to terminate?"}
  ]
}

=== EXAMPLE 3: Conversational / indirect prompt ===
User request: "We're hiring a landscaping company to maintain our office grounds. Green Thumb Landscaping will do weekly lawn care and seasonal planting for $800 a month. We're in Florida."

Correct extraction:
{
  "known_answers": {
    "service_type": "Standard",
    "contractor_name": "Green Thumb Landscaping",
    "services_description": "weekly lawn care and seasonal planting",
    "compensation_amount": "$800 per month",
    "governing_law": "Florida"
  },
  "field_evidence": {
    "service_type": "hiring a landscaping company",
    "contractor_name": "Green Thumb Landscaping",
    "services_description": "weekly lawn care and seasonal planting",
    "compensation_amount": "$800 a month",
    "governing_law": "We're in Florida"
  },
  "follow_up_questions": [
    {"field": "client_name", "question": "What is the client company's name?"},
    {"field": "client_address", "question": "What is the client's address?"},
    {"field": "contractor_address", "question": "What is the address for Green Thumb Landscaping?"},
    {"field": "effective_date", "question": "When should the service agreement take effect?"},
    {"field": "payment_schedule", "question": "Payment Schedule? (Upon Completion / Net 30 / Monthly / Milestone-Based)"},
    {"field": "termination_notice_days", "question": "How many days written notice should be required to terminate?"},
    {"field": "ip_ownership", "question": "Who should own the intellectual property created? (Client / Contractor / Shared)"}
  ]
}
"""

FEW_SHOT_BY_TYPE = {
    "NDA": NDA_FEW_SHOT_EXAMPLES,
    "ConsultingAgreement": CONSULTING_FEW_SHOT_EXAMPLES,
    "EmploymentAgreement": EMPLOYMENT_FEW_SHOT_EXAMPLES,
    "ServiceAgreement": SERVICE_FEW_SHOT_EXAMPLES,
}

FEW_SHOT_RULES = """
Key rules shown by examples:
- Do NOT include a field in known_answers if you cannot point to specific words in the user's request that support it.
- Do NOT guess addresses, emails, or values if the user did not state them.
- Leave fields OUT of known_answers entirely when unsupported (do not set them to empty strings).
- Always provide follow_up_questions for ALL required fields missing from known_answers.
- Every field in known_answers MUST have a matching entry in field_evidence.
"""

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the intake extraction layer for a deterministic contract drafting system.

Your job:
1. Read the user's plain-English contract request carefully.
2. Extract ONLY values that are explicitly stated or clearly implied in the user's words.
3. For EVERY extracted value, provide the exact snippet from the user's input as evidence in field_evidence.
4. For ALL required fields that are missing or ambiguous, add a clear follow_up_question.

Critical rules:
- Use ONLY field names from the canonical schema provided.
- Do NOT invent new fields.
- Do NOT guess or infer values not directly supported by the user's words.
- Omit unsupported fields from known_answers entirely — do NOT set them to empty strings.
- Every field in known_answers MUST have a corresponding entry in field_evidence.
- Every required field NOT in known_answers MUST have a follow_up_question.
- Return valid JSON matching the provided schema.
"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
def build_schema_summary(contract_type: str = DEFAULT_CONTRACT_TYPE) -> str:
    fields_info = []
    for f in schema_fields(contract_type):
        name = field_name(f)
        entry = {
            "name": name,
            "label": f.get("label", name),
            "type": f.get("type", "text"),
            "required": f.get("required", False),
        }
        if f.get("options"):
            entry["options"] = f["options"]
        fields_info.append(entry)
    return json.dumps(fields_info, indent=2)


def build_user_prompt(user_prompt: str, contract_type: str = DEFAULT_CONTRACT_TYPE) -> str:
    examples = FEW_SHOT_BY_TYPE.get(contract_type, FEW_SHOT_BY_TYPE["NDA"])
    return (
        "Canonical questionnaire fields:\n"
        f"{build_schema_summary(contract_type)}\n\n"
        f"{examples}\n\n"
        f"{FEW_SHOT_RULES}\n\n"
        "Now extract from this user request. Return JSON only.\n\n"
        f"User request: \"{user_prompt}\""
    )


# ---------------------------------------------------------------------------
# Normalization (contract-type agnostic — driven by field type in schema)
# ---------------------------------------------------------------------------
def normalize_value_for_field(value: Any, field: dict) -> Tuple[Any, bool]:
    field_type = field.get("type", "text")
    options = field.get("options", []) or []

    if value is None:
        return None, False

    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None, False

    if field_type == "number":
        try:
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned.isdigit():
                    return int(cleaned), True
                return float(cleaned), True
            if isinstance(value, (int, float)):
                return value, True
        except Exception:
            return None, False
        return None, False

    if field_type == "select":
        if not isinstance(value, str):
            return None, False
        if not options:
            return value, True
        for opt in options:
            if value.strip().lower() == str(opt).strip().lower():
                return opt, True
        return None, False

    if field_type == "email":
        if isinstance(value, str) and "@" in value:
            match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', value)
            if match:
                return match.group(0), True
        return None, False

    # default text
    if isinstance(value, (int, float, bool)):
        return str(value), True
    if isinstance(value, str) and value != "":
        return value, True
    return None, False


def generic_follow_up_question(field: dict) -> str:
    label = field.get("label", field_name(field))
    options = field.get("options", []) or []
    help_text = field.get("help_text", field.get("help", "")).strip()

    if options:
        option_text = " / ".join(str(o) for o in options)
        return f"{label}? ({option_text})"
    if help_text:
        return f"{label}? {help_text}"
    return f"What is the correct value for {label}?"


# ---------------------------------------------------------------------------
# Extraction (single pass — no reviewer)
# ---------------------------------------------------------------------------
def extract_answers_from_prompt(user_prompt: str, contract_type: str = DEFAULT_CONTRACT_TYPE, model: str = MODEL) -> dict:
    response = chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(user_prompt, contract_type)},
        ],
        format=build_extraction_schema(contract_type),
        think=False,
    )
    raw = response.message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        return {"known_answers": {}, "field_evidence": {}, "follow_up_questions": []}


# ---------------------------------------------------------------------------
# Verify and prepare follow-ups
#
# Conservative strategy:
# - Only keep a field if it exists in the schema, has evidence, and normalizes
# - Every missing required field gets a follow-up question, no exceptions
# ---------------------------------------------------------------------------
def verify_and_prepare(extraction: dict, contract_type: str = DEFAULT_CONTRACT_TYPE) -> Tuple[Dict[str, Any], List[dict], Dict[str, str]]:
    answers = extraction.get("known_answers", {}) or {}
    evidence = extraction.get("field_evidence", {}) or {}
    follow_ups_raw = extraction.get("follow_up_questions", []) or []

    # Strip empty strings the model sometimes produces
    answers = {k: v for k, v in answers.items() if v is not None and str(v).strip() != ""}
    evidence = {k: v for k, v in evidence.items() if v is not None and str(v).strip() != ""}

    # Normalize against schema — require evidence for every field
    verified_answers: Dict[str, Any] = {}
    verified_evidence: Dict[str, str] = {}
    lookup = field_lookup(contract_type)

    for name, raw_value in answers.items():
        field = lookup.get(name)
        if not field:
            continue

        support = str(evidence.get(name, "")).strip()
        if not support:
            continue

        normalized_value, ok = normalize_value_for_field(raw_value, field)
        if not ok:
            continue

        verified_answers[name] = normalized_value
        verified_evidence[name] = support

    # Build follow-up questions for ALL missing required fields
    # Prefer the model's question if it generated one, else use a generic one
    follow_up_by_field = {
        item["field"]: item["question"]
        for item in follow_ups_raw
        if "field" in item and "question" in item
    }
    final_follow_ups: List[dict] = []
    for req in required_fields(contract_type):
        if req not in verified_answers:
            field = lookup[req]
            question = follow_up_by_field.get(req) or generic_follow_up_question(field)
            final_follow_ups.append({"field": req, "question": question})

    return verified_answers, final_follow_ups, verified_evidence


def add_derived_defaults(answers: Dict[str, Any], contract_type: str = DEFAULT_CONTRACT_TYPE) -> Dict[str, Any]:
    if contract_type == "NDA":
        if not answers.get("front_page_email_addresses"):
            a = str(answers.get("party_a_email", "")).strip()
            b = str(answers.get("party_b_email", "")).strip()
            answers["front_page_email_addresses"] = " / ".join([x for x in [a, b] if x])
    if not answers.get("dispute_resolution_method"):
        answers["dispute_resolution_method"] = "Litigation"
    if not answers.get("term_end_date"):
        if contract_type == "ServiceAgreement":
            answers["term_end_date"] = "the completion of the Services"
        elif contract_type == "EmploymentAgreement":
            answers["term_end_date"] = "the termination of employment"
    if "special_provisions" not in answers:
        answers["special_provisions"] = ""
    return answers
