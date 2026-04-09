"""
Automated test harness for the LexiAgent extraction pipeline.
Supports NDA, ConsultingAgreement, and EmploymentAgreement test prompts, scores extraction accuracy.

Scoring philosophy:
- Conservative extraction is fine — we don't penalize missing fields heavily
- Follow-up coverage is critical — every missing required field MUST get a question
- Follow-up question quality matters — questions must be clear and answerable
- Hallucination is the worst failure — extracting unsupported values is penalized hard

Usage:
    python app/test_extraction.py                    # run all NDA tests
    python app/test_extraction.py consulting         # run all consulting tests
    python app/test_extraction.py employment         # run all employment tests
    python app/test_extraction.py employment 1       # run employment test 1
    python app/test_extraction.py nda 1              # run NDA test 1
    python app/test_extraction.py all                # run all tests (all types)
"""

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from run_intake_loop import (
    extract_answers_from_prompt,
    verify_and_prepare,
    add_derived_defaults,
    MODEL,
)
from assemble_contract import assemble_contract

# ---------------------------------------------------------------------------
# Test definitions from the handoff document (Section 16-17)
# ---------------------------------------------------------------------------
TESTS = [
    {
        "id": 1,
        "name": "Fully specified unilateral",
        "prompt": (
            "We need a unilateral NDA between Acme Corp., a Delaware corporation, "
            "and Beta Ventures LLC, a Connecticut LLC, to evaluate a potential partnership "
            "for 3 years under Connecticut law using litigation. Acme's email is "
            "legal@acme.com and Beta's email is ops@betaventures.com."
        ),
        "expected_extracted": {
            "nda_type": "Unilateral",
            "party_a_name": "Acme Corp.",
            "party_a_entity_details": "Delaware corporation",
            "party_a_email": "legal@acme.com",
            "party_b_name": "Beta Ventures LLC",
            "party_b_entity_details": "Connecticut LLC",
            "party_b_email": "ops@betaventures.com",
            "purpose": "evaluate a potential partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Litigation",
        },
        "expected_follow_ups": [],
        # Fields the user DID NOT mention — extracting these is hallucination
        "must_not_extract": [],
    },
    {
        "id": 2,
        "name": "Partially specified unilateral",
        "prompt": (
            "We need a unilateral NDA between Acme Corp and Beta Ventures LLC to evaluate "
            "a potential partnership for 3 years under Connecticut law using litigation."
        ),
        "expected_extracted": {
            "nda_type": "Unilateral",
            "party_a_name": "Acme Corp",
            "party_b_name": "Beta Ventures LLC",
            "purpose": "evaluate a potential partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Litigation",
        },
        "expected_follow_ups": [
            "party_a_entity_details",
            "party_a_email",
            "party_b_entity_details",
            "party_b_email",
        ],
        "must_not_extract": [
            "party_a_entity_details",
            "party_a_email",
            "party_b_entity_details",
            "party_b_email",
        ],
    },
    {
        "id": 3,
        "name": "Role-based unilateral (implies unilateral)",
        "prompt": (
            "Acme Corp wants to disclose confidential information to Beta Ventures LLC "
            "so they can evaluate a potential partnership. The arrangement should last 3 years, "
            "use Connecticut law, and resolve disputes through litigation. Acme's email is "
            "legal@acme.com and Beta's email is ops@betaventures.com."
        ),
        "expected_extracted": {
            "party_a_name": "Acme Corp",
            "party_a_email": "legal@acme.com",
            "party_b_name": "Beta Ventures LLC",
            "party_b_email": "ops@betaventures.com",
            "purpose": "evaluate a potential partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Litigation",
        },
        "expected_follow_ups": [
            "party_a_entity_details",
            "party_b_entity_details",
        ],
        "acceptable_extra_extracted": ["nda_type"],
        "must_not_extract": [
            "party_a_entity_details",
            "party_b_entity_details",
        ],
    },
    {
        "id": 4,
        "name": "Fully specified mutual",
        "prompt": (
            "We need a mutual NDA between Acme Corp., a Delaware corporation, and Beta Ventures LLC, "
            "a Connecticut LLC, to evaluate a potential partnership for 3 years under Connecticut law "
            "using litigation. Acme's email is legal@acme.com and Beta's email is ops@betaventures.com."
        ),
        "expected_extracted": {
            "nda_type": "Mutual",
            "party_a_name": "Acme Corp.",
            "party_a_entity_details": "Delaware corporation",
            "party_a_email": "legal@acme.com",
            "party_b_name": "Beta Ventures LLC",
            "party_b_entity_details": "Connecticut LLC",
            "party_b_email": "ops@betaventures.com",
            "purpose": "evaluate a potential partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Litigation",
        },
        "expected_follow_ups": [],
        "must_not_extract": [],
    },
    {
        "id": 5,
        "name": "Partially specified mutual",
        "prompt": (
            "We need a mutual NDA between Acme Corp and Beta Ventures LLC for evaluating a "
            "potential partnership for 3 years under Connecticut law."
        ),
        "expected_extracted": {
            "nda_type": "Mutual",
            "party_a_name": "Acme Corp",
            "party_b_name": "Beta Ventures LLC",
            "purpose": "evaluating a potential partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
        },
        "expected_follow_ups": [
            "party_a_entity_details",
            "party_a_email",
            "party_b_entity_details",
            "party_b_email",
            "dispute_resolution_method",
        ],
        "must_not_extract": [
            "party_a_entity_details",
            "party_a_email",
            "party_b_entity_details",
            "party_b_email",
            "dispute_resolution_method",
        ],
    },
    {
        "id": 6,
        "name": "Conversational / indirect",
        "prompt": (
            "Acme Corp and Beta Ventures LLC are exploring a possible partnership and need an NDA. "
            "It should last 3 years, follow Connecticut law, and use litigation if there's a dispute. "
            "Acme's email is legal@acme.com and Beta's email is ops@betaventures.com."
        ),
        "expected_extracted": {
            "party_a_name": "Acme Corp",
            "party_a_email": "legal@acme.com",
            "party_b_name": "Beta Ventures LLC",
            "party_b_email": "ops@betaventures.com",
            "purpose": "exploring a possible partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Litigation",
        },
        "expected_follow_ups": [
            "nda_type",
            "party_a_entity_details",
            "party_b_entity_details",
        ],
        "must_not_extract": [
            "nda_type",
            "party_a_entity_details",
            "party_b_entity_details",
        ],
    },
    # ------------------------------------------------------------------
    # Edge-case tests 7-10
    # ------------------------------------------------------------------
    {
        "id": 7,
        "name": "Typos and abbreviations",
        "prompt": (
            "We need a unilateral NDA btwn Acme Corp and Beta Ventures for evaluating "
            "a partnership, 3 yrs, Connecticut law, litigation. "
            "Acme email: legal@acme.com, Beta email: ops@betaventures.com."
        ),
        "expected_extracted": {
            "party_a_name": "Acme Corp",
            "party_b_name": "Beta Ventures",
            "purpose": "evaluating a partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Litigation",
            "party_a_email": "legal@acme.com",
            "party_b_email": "ops@betaventures.com",
        },
        "expected_follow_ups": [
            "party_a_entity_details",
            "party_b_entity_details",
        ],
        "acceptable_extra_extracted": ["nda_type"],
        "must_not_extract": [
            "party_a_entity_details",
            "party_b_entity_details",
        ],
    },
    {
        "id": 8,
        "name": "Extra irrelevant information",
        "prompt": (
            "We need a mutual NDA between Acme Corp., a Delaware corporation, and Beta Ventures LLC, "
            "a Connecticut LLC. Purpose is evaluating a potential partnership for 3 years under "
            "Connecticut law using arbitration. Acme's email is legal@acme.com and Beta's email is "
            "ops@betaventures.com. The CEO's name is John Smith, they prefer blue ink signatures, "
            "and the office is at 123 Main St."
        ),
        "expected_extracted": {
            "nda_type": "Mutual",
            "party_a_name": "Acme Corp.",
            "party_a_entity_details": "Delaware corporation",
            "party_a_email": "legal@acme.com",
            "party_b_name": "Beta Ventures LLC",
            "party_b_entity_details": "Connecticut LLC",
            "party_b_email": "ops@betaventures.com",
            "purpose": "evaluating a potential partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Arbitration",
        },
        "expected_follow_ups": [],
        "must_not_extract": [],
    },
    {
        "id": 9,
        "name": "Minimal prompt",
        "prompt": "NDA between Acme and Beta.",
        "expected_extracted": {
            "party_a_name": "Acme",
            "party_b_name": "Beta",
        },
        "expected_follow_ups": [
            "nda_type",
            "party_a_entity_details",
            "party_a_email",
            "party_b_entity_details",
            "party_b_email",
            "purpose",
            "confidentiality_period_number",
            "confidentiality_period_unit",
            "governing_law",
            "dispute_resolution_method",
        ],
        "must_not_extract": [
            "nda_type",
            "party_a_entity_details",
            "party_a_email",
            "party_b_entity_details",
            "party_b_email",
            "purpose",
            "confidentiality_period_number",
            "confidentiality_period_unit",
            "governing_law",
            "dispute_resolution_method",
        ],
    },
    {
        "id": 10,
        "name": "Conflicting info (mutual + disclose)",
        "prompt": (
            "We need a mutual NDA. Acme Corp wants to disclose confidential information to "
            "Beta Ventures LLC for evaluating a potential partnership. 3 years, Connecticut law, "
            "litigation. Acme's email is legal@acme.com and Beta's email is ops@betaventures.com."
        ),
        "expected_extracted": {
            "party_a_name": "Acme Corp",
            "party_b_name": "Beta Ventures LLC",
            "purpose": "evaluating a potential partnership",
            "confidentiality_period_number": 3,
            "confidentiality_period_unit": "years",
            "governing_law": "Connecticut",
            "dispute_resolution_method": "Litigation",
            "party_a_email": "legal@acme.com",
            "party_b_email": "ops@betaventures.com",
        },
        # nda_type is ambiguous — user says "mutual" but describes unilateral behavior
        # Conservative: should either extract "Mutual" (trusting explicit statement) or ask
        "expected_follow_ups": [
            "party_a_entity_details",
            "party_b_entity_details",
        ],
        "acceptable_extra_extracted": ["nda_type"],
        "must_not_extract": [
            "party_a_entity_details",
            "party_b_entity_details",
        ],
    },
]


# ---------------------------------------------------------------------------
# Consulting Agreement tests
# ---------------------------------------------------------------------------
CONSULTING_TESTS = [
    {
        "id": 1,
        "name": "Fully specified consulting agreement",
        "contract_type": "ConsultingAgreement",
        "prompt": (
            "We need a consulting agreement between Acme Corp at 123 Main St, Hartford CT 06103 "
            "(legal@acme.com) and Jane Smith Consulting at 456 Oak Ave, New Haven CT 06510 "
            "(jane@smithconsulting.com). Jane will provide marketing strategy and brand positioning "
            "services for $25,000, paid net 30. The agreement starts January 15, 2026 and ends "
            "June 30, 2026. Either party can terminate with 30 days notice. Connecticut law, "
            "arbitration for disputes. Client owns all IP."
        ),
        "expected_extracted": {
            "consulting_type": "Standard",
            "client_name": "Acme Corp",
            "client_address": "123 Main St, Hartford CT 06103",
            "client_email": "legal@acme.com",
            "consultant_name": "Jane Smith Consulting",
            "consultant_address": "456 Oak Ave, New Haven CT 06510",
            "consultant_email": "jane@smithconsulting.com",
            "effective_date": "January 15, 2026",
            "services_description": "marketing strategy and brand positioning",
            "compensation_amount": "$25,000",
            "payment_schedule": "Net 30",
            "term_end_date": "June 30, 2026",
            "termination_notice_days": 30,
            "governing_law": "Connecticut",
            "ip_ownership": "Client",
            "dispute_resolution_method": "Arbitration",
        },
        "expected_follow_ups": [],
        "must_not_extract": [],
    },
    {
        "id": 2,
        "name": "Partially specified consulting (missing addresses/emails/dates)",
        "contract_type": "ConsultingAgreement",
        "prompt": (
            "I need a consulting agreement. BrightWave Marketing will provide social media management "
            "and content creation for TechStart Inc. The fee is $5,000 per month. Use New York law. "
            "The consultant keeps the IP."
        ),
        "expected_extracted": {
            "consulting_type": "Standard",
            "client_name": "TechStart Inc",
            "consultant_name": "BrightWave Marketing",
            "services_description": "social media management and content creation",
            "compensation_amount": "$5,000",
            "governing_law": "New York",
            "ip_ownership": "Consultant",
        },
        "expected_follow_ups": [
            "client_address",
            "client_email",
            "consultant_address",
            "consultant_email",
            "effective_date",
            "term_end_date",
            "termination_notice_days",
        ],
        "acceptable_extra_extracted": ["payment_schedule"],
        "must_not_extract": [
            "client_address",
            "client_email",
            "consultant_address",
            "consultant_email",
            "effective_date",
            "term_end_date",
            "termination_notice_days",
        ],
    },
    {
        "id": 3,
        "name": "Conversational consulting prompt",
        "contract_type": "ConsultingAgreement",
        "prompt": (
            "We're hiring a consultant to help with our website redesign. The company is called "
            "PixelPerfect Design and they'll charge us $150 an hour. We want to use California law. "
            "My company is GlobalTech Solutions and my email is cto@globaltech.com."
        ),
        "expected_extracted": {
            "client_name": "GlobalTech Solutions",
            "client_email": "cto@globaltech.com",
            "consultant_name": "PixelPerfect Design",
            "services_description": "website redesign",
            "compensation_amount": "$150",
            "governing_law": "California",
        },
        "expected_follow_ups": [
            "client_address",
            "consultant_address",
            "consultant_email",
            "effective_date",
            "payment_schedule",
            "term_end_date",
            "termination_notice_days",
            "ip_ownership",
        ],
        "acceptable_extra_extracted": ["consulting_type"],
        "must_not_extract": [
            "client_address",
            "consultant_address",
            "consultant_email",
            "effective_date",
            "term_end_date",
            "termination_notice_days",
        ],
    },
    {
        "id": 4,
        "name": "Minimal consulting prompt",
        "contract_type": "ConsultingAgreement",
        "prompt": "Consulting agreement between DataPro Analytics and Summit Corp.",
        "expected_extracted": {
            "consultant_name": "DataPro Analytics",
            "client_name": "Summit Corp",
        },
        "expected_follow_ups": [
            "client_address",
            "client_email",
            "consultant_address",
            "consultant_email",
            "effective_date",
            "services_description",
            "compensation_amount",
            "payment_schedule",
            "term_end_date",
            "termination_notice_days",
            "governing_law",
            "ip_ownership",
        ],
        "acceptable_extra_extracted": ["consulting_type"],
        "must_not_extract": [
            "client_address",
            "client_email",
            "consultant_address",
            "consultant_email",
            "effective_date",
            "services_description",
            "compensation_amount",
            "payment_schedule",
            "term_end_date",
            "termination_notice_days",
            "governing_law",
            "ip_ownership",
        ],
    },
]


# ---------------------------------------------------------------------------
# Employment Agreement tests
# ---------------------------------------------------------------------------
EMPLOYMENT_TESTS = [
    {
        "id": 1,
        "name": "Fully specified employment agreement",
        "contract_type": "EmploymentAgreement",
        "prompt": (
            "We need an employment agreement for TechCorp Inc. at 100 Innovation Dr, San Francisco, CA 94105 "
            "hiring Jane Smith of 456 Oak Ave, San Francisco, CA 94110 as a Senior Software Engineer. "
            "She will design, develop, and maintain software applications. Full-time, starting January 15, 2026. "
            "Salary is $120,000 per year paid bi-weekly. She'll work at 100 Innovation Dr, San Francisco, CA 94105. "
            "30 days notice to terminate. California law, arbitration for disputes."
        ),
        "expected_extracted": {
            "employment_type": "Standard",
            "employer_name": "TechCorp Inc.",
            "employer_address": "100 Innovation Dr, San Francisco, CA 94105",
            "employee_name": "Jane Smith",
            "employee_address": "456 Oak Ave, San Francisco, CA 94110",
            "job_title": "Senior Software Engineer",
            "job_duties": "design, develop, and maintain software applications",
            "start_date": "January 15, 2026",
            "employment_basis": "Full-Time",
            "compensation_amount": "$120,000 per year",
            "pay_frequency": "Bi-Weekly",
            "work_location": "100 Innovation Dr, San Francisco, CA 94105",
            "termination_notice_days": 30,
            "governing_law": "California",
            "dispute_resolution_method": "Arbitration",
        },
        "expected_follow_ups": [],
        "must_not_extract": [],
    },
    {
        "id": 2,
        "name": "Partially specified employment (missing addresses/duties)",
        "contract_type": "EmploymentAgreement",
        "prompt": (
            "I need to hire a Marketing Manager at Bright Solutions LLC. The salary is "
            "$85,000 per year, paid semi-monthly. The job starts on March 1, 2026. "
            "We're in Texas. 14 days notice to terminate."
        ),
        "expected_extracted": {
            "employer_name": "Bright Solutions LLC",
            "job_title": "Marketing Manager",
            "start_date": "March 1, 2026",
            "compensation_amount": "$85,000",
            "pay_frequency": "Semi-Monthly",
            "governing_law": "Texas",
            "termination_notice_days": 14,
        },
        "expected_follow_ups": [
            "employer_address",
            "employee_name",
            "employee_address",
            "job_duties",
            "employment_basis",
            "work_location",
        ],
        "acceptable_extra_extracted": ["employment_type"],
        "must_not_extract": [
            "employer_address",
            "employee_name",
            "employee_address",
            "job_duties",
            "work_location",
        ],
    },
    {
        "id": 3,
        "name": "Conversational employment prompt",
        "contract_type": "EmploymentAgreement",
        "prompt": (
            "We're bringing on a new data analyst at our company, DataFlow Analytics. "
            "They'll be working part-time, $35 an hour, paid weekly. We need this under "
            "New York law. 30 days notice for termination."
        ),
        "expected_extracted": {
            "employer_name": "DataFlow Analytics",
            "job_title": "Data Analyst",
            "employment_basis": "Part-Time",
            "compensation_amount": "$35",
            "pay_frequency": "Weekly",
            "governing_law": "New York",
            "termination_notice_days": 30,
        },
        "expected_follow_ups": [
            "employer_address",
            "employee_name",
            "employee_address",
            "job_duties",
            "start_date",
            "work_location",
        ],
        "acceptable_extra_extracted": ["employment_type"],
        "must_not_extract": [
            "employer_address",
            "employee_name",
            "employee_address",
            "job_duties",
            "start_date",
            "work_location",
        ],
    },
    {
        "id": 4,
        "name": "Minimal employment prompt",
        "contract_type": "EmploymentAgreement",
        "prompt": "Employment agreement between Acme Inc and John Doe.",
        "expected_extracted": {
            "employer_name": "Acme Inc",
            "employee_name": "John Doe",
        },
        "expected_follow_ups": [
            "employer_address",
            "employee_address",
            "job_title",
            "job_duties",
            "start_date",
            "employment_basis",
            "compensation_amount",
            "pay_frequency",
            "work_location",
            "termination_notice_days",
            "governing_law",
        ],
        "acceptable_extra_extracted": ["employment_type"],
        "must_not_extract": [
            "employer_address",
            "employee_address",
            "job_title",
            "job_duties",
            "start_date",
            "compensation_amount",
            "pay_frequency",
            "work_location",
            "termination_notice_days",
            "governing_law",
        ],
    },
]

SERVICE_TESTS = [
    {
        "id": 1,
        "name": "Fully specified service agreement",
        "contract_type": "ServiceAgreement",
        "prompt": (
            "We need a service agreement between GlobalTech Solutions at 500 Market St, San Francisco CA 94105 "
            "and CleanPro Services LLC at 200 Oak Blvd, Oakland CA 94612. CleanPro will provide commercial "
            "office cleaning including daily janitorial and weekly deep cleaning for $3,500 per month, paid monthly. "
            "Starting February 1, 2026. 30 days notice to terminate. California law, mediation for disputes. Client owns all IP."
        ),
        "expected_extracted": {
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
            "dispute_resolution_method": "Mediation",
        },
        "expected_follow_ups": [],
        "acceptable_extra_extracted": ["service_type"],
    },
    {
        "id": 2,
        "name": "Partial service agreement - missing addresses",
        "contract_type": "ServiceAgreement",
        "prompt": (
            "I need a service agreement. WebWorks Design will build a new website for Summit Corp. "
            "The project costs $15,000 total, to be paid upon completion. Use Texas law."
        ),
        "expected_extracted": {
            "service_type": "Standard",
            "client_name": "Summit Corp",
            "contractor_name": "WebWorks Design",
            "services_description": "build a new website",
            "compensation_amount": "$15,000",
            "payment_schedule": "Upon Completion",
            "governing_law": "Texas",
        },
        "expected_follow_ups": [
            "client_address",
            "contractor_address",
            "effective_date",
            "termination_notice_days",
            "ip_ownership",
        ],
        "acceptable_extra_extracted": ["service_type"],
    },
    {
        "id": 3,
        "name": "Conversational service agreement",
        "contract_type": "ServiceAgreement",
        "prompt": (
            "We're hiring a landscaping company to maintain our office grounds. "
            "Green Thumb Landscaping will do weekly lawn care and seasonal planting for $800 a month. "
            "We're in Florida."
        ),
        "expected_extracted": {
            "contractor_name": "Green Thumb Landscaping",
            "services_description": "weekly lawn care and seasonal planting",
            "compensation_amount": "$800",
            "governing_law": "Florida",
        },
        "expected_follow_ups": [
            "client_name",
            "client_address",
            "contractor_address",
            "effective_date",
            "termination_notice_days",
            "ip_ownership",
        ],
        "acceptable_extra_extracted": ["service_type", "payment_schedule"],
    },
    {
        "id": 4,
        "name": "Near-empty service agreement prompt",
        "contract_type": "ServiceAgreement",
        "prompt": "I need a service contract for some IT consulting work.",
        "expected_extracted": {},
        "expected_follow_ups": [
            "client_name",
            "client_address",
            "contractor_name",
            "contractor_address",
            "services_description",
            "effective_date",
            "compensation_amount",
            "payment_schedule",
            "termination_notice_days",
            "governing_law",
            "ip_ownership",
        ],
        "acceptable_extra_extracted": ["service_type", "services_description"],
        "must_not_extract": [
            "client_name",
            "client_address",
            "contractor_name",
            "contractor_address",
            "effective_date",
            "compensation_amount",
            "termination_notice_days",
            "governing_law",
        ],
    },
]


# ---------------------------------------------------------------------------
# Value comparison
# ---------------------------------------------------------------------------
def normalize_for_comparison(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = " ".join(s.split())
    s = s.rstrip(".")
    s = s.replace("the state of ", "")
    if s.endswith(" law"):
        s = s[:-4].strip()
    return s


def values_match(expected, actual) -> bool:
    e = normalize_for_comparison(expected)
    a = normalize_for_comparison(actual)
    if not e or not a:
        return e == a
    if e == a:
        return True
    if e in a or a in e:
        return True
    try:
        if float(e) == float(a):
            return True
    except (ValueError, TypeError):
        pass
    return False


# ---------------------------------------------------------------------------
# Follow-up question quality check
# ---------------------------------------------------------------------------
def check_question_quality(field_name_str: str, question: str) -> dict:
    """Check if a follow-up question is clear and answerable."""
    issues = []

    if not question or len(question.strip()) < 5:
        issues.append("question is empty or too short")

    if question and not question.strip().endswith("?") and not question.strip().endswith(")"):
        issues.append("question doesn't end with ? or option list")

    # Check that question references something relevant to the field
    field_keywords = field_name_str.replace("_", " ").lower().split()
    question_lower = question.lower()
    has_relevance = any(kw in question_lower for kw in field_keywords if len(kw) > 2)
    if not has_relevance:
        issues.append(f"question may not relate to field '{field_name_str}'")

    return {
        "field": field_name_str,
        "question": question,
        "quality": "good" if not issues else "poor",
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_test(test: dict, verified_answers: dict, follow_ups: List[dict]) -> dict:
    expected = test["expected_extracted"]
    expected_fups = set(test.get("expected_follow_ups", []))
    must_not_extract = set(test.get("must_not_extract", []))
    acceptable_extra = set(test.get("acceptable_extra_extracted", []))
    actual_fup_fields = {item["field"] for item in follow_ups}

    results = {
        "test_id": test["id"],
        "test_name": test["name"],
        "field_results": {},
        "follow_up_results": {},
        "follow_up_quality": [],
    }

    # --- Extraction scoring ---
    total_expected = len(expected)
    correct_fields = 0
    field_details = {}

    for fname, expected_val in expected.items():
        actual_val = verified_answers.get(fname)
        match = values_match(expected_val, actual_val)
        if match:
            correct_fields += 1
        field_details[fname] = {
            "expected": expected_val,
            "actual": actual_val,
            "match": match,
        }

    # --- Hallucination detection ---
    hallucinated = []
    for fname in verified_answers:
        if fname in must_not_extract:
            hallucinated.append(fname)

    # --- Follow-up coverage (the critical metric) ---
    fup_total = len(expected_fups)
    fup_correct = len(expected_fups & actual_fup_fields)
    fup_missing = expected_fups - actual_fup_fields

    fup_details = {}
    for fname in expected_fups:
        fup_details[fname] = {
            "expected": "must be asked",
            "was_asked": fname in actual_fup_fields,
        }

    # --- Follow-up question quality ---
    fup_quality = []
    for item in follow_ups:
        quality = check_question_quality(item["field"], item["question"])
        fup_quality.append(quality)

    good_questions = sum(1 for q in fup_quality if q["quality"] == "good")
    total_questions = len(fup_quality) if fup_quality else 1

    # --- Scoring weights ---
    # Extraction: 30% (conservative is OK)
    # Follow-up coverage: 50% (critical — must catch all missing fields)
    # No hallucination: 20% (must not fabricate)
    extraction_score = correct_fields / total_expected if total_expected > 0 else 1.0
    followup_coverage = fup_correct / fup_total if fup_total > 0 else 1.0
    hallucination_score = 1.0 - (len(hallucinated) * 0.25)  # 25% penalty each
    hallucination_score = max(0, hallucination_score)
    question_quality_score = good_questions / total_questions

    overall = (
        extraction_score * 0.30
        + followup_coverage * 0.50
        + hallucination_score * 0.20
    )

    results["field_results"] = field_details
    results["follow_up_results"] = fup_details
    results["follow_up_quality"] = fup_quality
    results["hallucinated_fields"] = hallucinated
    results["follow_up_missing"] = list(fup_missing)
    results["extraction_score"] = round(extraction_score, 3)
    results["followup_coverage"] = round(followup_coverage, 3)
    results["hallucination_score"] = round(hallucination_score, 3)
    results["question_quality_score"] = round(question_quality_score, 3)
    results["overall_score"] = round(overall, 3)

    return results


# ---------------------------------------------------------------------------
# Assembly test (end-to-end)
# ---------------------------------------------------------------------------
NDA_FOLLOW_UP_ANSWERS = {
    "nda_type": "Unilateral",
    "party_a_name": "Acme Corp.",
    "party_a_entity_details": "Delaware corporation",
    "party_a_email": "legal@acme.com",
    "party_b_name": "Beta Ventures LLC",
    "party_b_entity_details": "Connecticut LLC",
    "party_b_email": "ops@betaventures.com",
    "purpose": "evaluating a potential partnership",
    "confidentiality_period_number": 3,
    "confidentiality_period_unit": "years",
    "governing_law": "Connecticut",
    "dispute_resolution_method": "Litigation",
}

CONSULTING_FOLLOW_UP_ANSWERS = {
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
    "termination_notice_days": 30,
    "governing_law": "Connecticut",
    "ip_ownership": "Client",
    "dispute_resolution_method": "Arbitration",
}

EMPLOYMENT_FOLLOW_UP_ANSWERS = {
    "employment_type": "Standard",
    "employer_name": "TechCorp Inc.",
    "employer_address": "100 Innovation Dr, San Francisco, CA 94105",
    "employee_name": "Jane Smith",
    "employee_address": "456 Oak Ave, San Francisco, CA 94110",
    "job_title": "Senior Software Engineer",
    "job_duties": "Design, develop, and maintain software applications",
    "start_date": "January 15, 2026",
    "employment_basis": "Full-Time",
    "compensation_amount": "$120,000 per year",
    "pay_frequency": "Bi-Weekly",
    "work_location": "100 Innovation Dr, San Francisco, CA 94105",
    "termination_notice_days": 30,
    "governing_law": "California",
    "dispute_resolution_method": "Arbitration",
}

SERVICE_FOLLOW_UP_ANSWERS = {
    "service_type": "Standard",
    "client_name": "GlobalTech Solutions Inc.",
    "client_address": "500 Market St, San Francisco CA 94105",
    "contractor_name": "CleanPro Services LLC",
    "contractor_address": "200 Oak Blvd, Oakland CA 94612",
    "services_description": "commercial office cleaning services",
    "effective_date": "February 1, 2026",
    "compensation_amount": "$3,500 per month",
    "payment_schedule": "Monthly",
    "termination_notice_days": 30,
    "governing_law": "California",
    "ip_ownership": "Client",
    "dispute_resolution_method": "Mediation",
}

FOLLOW_UP_ANSWERS_BY_TYPE = {
    "NDA": NDA_FOLLOW_UP_ANSWERS,
    "ConsultingAgreement": CONSULTING_FOLLOW_UP_ANSWERS,
    "EmploymentAgreement": EMPLOYMENT_FOLLOW_UP_ANSWERS,
    "ServiceAgreement": SERVICE_FOLLOW_UP_ANSWERS,
}


def fill_missing_and_assemble(verified_answers: dict, follow_ups: List[dict], contract_type: str = "NDA") -> tuple:
    fallback = FOLLOW_UP_ANSWERS_BY_TYPE.get(contract_type, NDA_FOLLOW_UP_ANSWERS)
    for item in follow_ups:
        fname = item["field"]
        if fname not in verified_answers and fname in fallback:
            verified_answers[fname] = fallback[fname]

    verified_answers = add_derived_defaults(verified_answers, contract_type)
    try:
        contract_text, unresolved, _ = assemble_contract(verified_answers, contract_type)
        return contract_text, unresolved
    except Exception as e:
        return f"ASSEMBLY ERROR: {e}", ["ERROR"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_tests(test_ids: Optional[List[int]] = None, contract_type: str = "NDA"):
    if contract_type == "all":
        # Run all contract type tests
        print("\n" + "=" * 60)
        print("  RUNNING NDA TESTS")
        print("=" * 60)
        nda_results, nda_avg = run_tests(test_ids=test_ids, contract_type="NDA")

        print("\n" + "=" * 60)
        print("  RUNNING CONSULTING TESTS")
        print("=" * 60)
        ca_results, ca_avg = run_tests(test_ids=test_ids, contract_type="ConsultingAgreement")

        print("\n" + "=" * 60)
        print("  RUNNING EMPLOYMENT TESTS")
        print("=" * 60)
        ea_results, ea_avg = run_tests(test_ids=test_ids, contract_type="EmploymentAgreement")

        print("\n" + "=" * 60)
        print("  RUNNING SERVICE TESTS")
        print("=" * 60)
        sa_results, sa_avg = run_tests(test_ids=test_ids, contract_type="ServiceAgreement")

        print(f"\n{'='*60}")
        print("COMBINED SUMMARY")
        print(f"{'='*60}")
        print(f"  NDA average:         {nda_avg*100:.1f}%")
        print(f"  Consulting average:  {ca_avg*100:.1f}%")
        print(f"  Employment average:  {ea_avg*100:.1f}%")
        print(f"  Service average:     {sa_avg*100:.1f}%")
        combined = (nda_avg + ca_avg + ea_avg + sa_avg) / 4
        print(f"  Combined average:    {combined*100:.1f}%")
        return nda_results + ca_results + ea_results + sa_results, combined

    test_bank_map = {
        "NDA": TESTS,
        "ConsultingAgreement": CONSULTING_TESTS,
        "EmploymentAgreement": EMPLOYMENT_TESTS,
        "ServiceAgreement": SERVICE_TESTS,
    }
    test_bank = test_bank_map.get(contract_type, TESTS)
    tests_to_run = test_bank if test_ids is None else [t for t in test_bank if t["id"] in test_ids]
    ct = contract_type  # shorthand

    all_results = []
    total_score = 0

    for test in tests_to_run:
        test_ct = test.get("contract_type", ct)
        print(f"\n{'='*60}")
        print(f"[{test_ct}] TEST {test['id']}: {test['name']}")
        print(f"{'='*60}")
        print(f"Prompt: {test['prompt'][:100]}...")

        start = time.time()
        print("  Extracting...")
        extraction = extract_answers_from_prompt(test["prompt"], test_ct)
        extract_time = time.time() - start

        raw_answers = extraction.get("known_answers", {})
        raw_answers = {k: v for k, v in raw_answers.items() if v is not None and str(v).strip() != ""}
        print(f"  Extracted {len(raw_answers)} fields in {extract_time:.1f}s")

        verify_start = time.time()
        verified_answers, follow_ups, evidence = verify_and_prepare(extraction, test_ct)
        verify_time = time.time() - verify_start
        total_time = time.time() - start

        print(f"  Verified {len(verified_answers)} fields, {len(follow_ups)} follow-ups")

        # Score
        result = score_test(test, verified_answers, follow_ups)
        result["contract_type"] = test_ct
        result["extract_time"] = round(extract_time, 1)
        result["total_time"] = round(total_time, 1)

        # Assembly
        contract_text, unresolved = fill_missing_and_assemble(dict(verified_answers), list(follow_ups), test_ct)
        result["assembly_success"] = len(unresolved) == 0
        result["unresolved_placeholders"] = unresolved

        all_results.append(result)
        total_score += result["overall_score"]

        # Print results
        print(f"\n  Verified answers: {json.dumps(verified_answers, indent=4, default=str)}")

        print(f"\n  Follow-ups ({len(follow_ups)}):")
        for fup in follow_ups:
            quality = next((q for q in result["follow_up_quality"] if q["field"] == fup["field"]), {})
            qmark = "OK" if quality.get("quality") == "good" else "!!"
            print(f"    [{qmark}] {fup['field']}: \"{fup['question']}\"")
            if quality.get("issues"):
                for issue in quality["issues"]:
                    print(f"         ^ {issue}")

        if result["hallucinated_fields"]:
            print(f"\n  !! HALLUCINATED: {result['hallucinated_fields']}")
        if result["follow_up_missing"]:
            print(f"  !! MISSING FOLLOW-UPS: {result['follow_up_missing']}")

        print(f"\n  Extraction:     {result['extraction_score']*100:.0f}% (weight 30%)")
        print(f"  Follow-up cov:  {result['followup_coverage']*100:.0f}% (weight 50%)")
        print(f"  No hallucinate: {result['hallucination_score']*100:.0f}% (weight 20%)")
        print(f"  Question qual:  {result['question_quality_score']*100:.0f}%")
        print(f"  Overall score:  {result['overall_score']*100:.0f}%")
        print(f"  Assembly:       {'PASS' if result['assembly_success'] else 'FAIL'}")
        if unresolved:
            print(f"  Unresolved:     {unresolved}")
        print(f"  Time:           {total_time:.1f}s")

        # Field misses
        for fname, detail in result["field_results"].items():
            if not detail["match"]:
                print(f"  MISS: {fname} expected={detail['expected']!r} got={detail['actual']!r}")

    # Summary
    avg_score = total_score / len(tests_to_run) if tests_to_run else 0
    assembly_pass = sum(1 for r in all_results if r["assembly_success"])

    # Count follow-up coverage across all tests
    total_expected_fups = 0
    total_actual_fups = 0
    total_hallucinations = 0
    for r in all_results:
        for fup_detail in r["follow_up_results"].values():
            total_expected_fups += 1
            if fup_detail["was_asked"]:
                total_actual_fups += 1
        total_hallucinations += len(r["hallucinated_fields"])

    print(f"\n{'='*60}")
    print(f"SUMMARY ({contract_type})")
    print(f"{'='*60}")
    for r in all_results:
        status = "PASS" if r["overall_score"] >= 0.8 else "FAIL"
        asm = "ASM:OK" if r["assembly_success"] else "ASM:FAIL"
        fup_status = f"FUP:{r['followup_coverage']*100:.0f}%"
        print(f"  Test {r['test_id']}: {r['overall_score']*100:5.1f}%  {status}  {asm}  {fup_status}  ({r['total_time']:.0f}s)  {r['test_name']}")

    print(f"\n  Average score:       {avg_score*100:.1f}%")
    print(f"  Assembly pass:       {assembly_pass}/{len(tests_to_run)}")
    print(f"  Follow-up coverage:  {total_actual_fups}/{total_expected_fups} ({total_actual_fups/total_expected_fups*100:.0f}%)" if total_expected_fups > 0 else "  Follow-up coverage:  N/A")
    print(f"  Hallucinations:      {total_hallucinations}")
    print(f"  Target:              95%+")

    # Save results
    suffix = f"_{contract_type.lower()}" if contract_type != "NDA" else ""
    output_path = ROOT / "output" / f"test_results{suffix}.json"
    output_path.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\n  Results saved to {output_path}")

    return all_results, avg_score


if __name__ == "__main__":
    contract_type = "NDA"
    test_ids = None

    for arg in sys.argv[1:]:
        if arg.isdigit():
            if test_ids is None:
                test_ids = []
            test_ids.append(int(arg))
        elif arg.lower() in ("consulting", "consultingagreement"):
            contract_type = "ConsultingAgreement"
        elif arg.lower() in ("employment", "employmentagreement"):
            contract_type = "EmploymentAgreement"
        elif arg.lower() in ("service", "serviceagreement"):
            contract_type = "ServiceAgreement"
        elif arg.lower() == "nda":
            contract_type = "NDA"
        elif arg.lower() == "all":
            contract_type = "all"

    run_tests(test_ids=test_ids, contract_type=contract_type)
