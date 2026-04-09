"""
Deterministic contract assembler.

Reads the contract registry to find the right config files for a given
contract type, then selects clauses, fills placeholders, and assembles
the final document.

Supports multiple contract types via config/contract_registry.json.
Each contract type has its own:
  - questionnaire schema
  - assembly order
  - placeholder mappings
  - clause library

The assembler does NOT use AI. It is purely deterministic.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "clause_library"
CONFIG_DIR = ROOT / "config"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> List[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Contract registry
# ---------------------------------------------------------------------------
def load_registry() -> dict:
    return load_json(CONFIG_DIR / "contract_registry.json")


def get_contract_config(contract_type: str) -> dict:
    """Look up a contract type in the registry and return its config paths."""
    registry = load_registry()
    types = registry.get("contract_types", {})
    if contract_type not in types:
        available = list(types.keys())
        raise ValueError(f"Unknown contract type '{contract_type}'. Available: {available}")
    return types[contract_type]


# ---------------------------------------------------------------------------
# Resource loading (registry-driven)
# ---------------------------------------------------------------------------
def load_resources(contract_type: str = "NDA") -> Tuple[List[dict], dict, dict, dict]:
    """Load clause library, assembly order, placeholder mappings, and config for a contract type."""
    config = get_contract_config(contract_type)
    library = load_jsonl(DATA_DIR / config["clause_library"])
    order = load_json(CONFIG_DIR / config["assembly_order"])
    mappings = load_json(CONFIG_DIR / config["placeholder_mappings"])
    return library, order, mappings, config


def load_questionnaire(contract_type: str = "NDA") -> dict:
    """Load the questionnaire schema for a contract type."""
    config = get_contract_config(contract_type)
    return load_json(CONFIG_DIR / config["questionnaire_schema"])


# ---------------------------------------------------------------------------
# Assembly logic (unchanged — contract-type agnostic)
# ---------------------------------------------------------------------------
def select_clauses(library: List[dict], subtype: str, subtype_field: str, ordered_clause_names: List[str]) -> List[dict]:
    """Select and order clauses from the library for a given subtype."""
    selected = []
    for clause_name in ordered_clause_names:
        # Match on the subtype field (e.g., nda_type for NDAs)
        candidates = [r for r in library if r.get(subtype_field) == subtype and r["clause_name"] == clause_name]
        if candidates:
            candidates.sort(key=lambda r: r.get("sort_order", 999))
            selected.append(candidates[0])
    return selected


def build_replacements(subtype: str, answers: Dict[str, str], placeholder_mappings: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """Build placeholder -> value replacements from answers."""
    mapping = placeholder_mappings[subtype]
    replacements = {}
    for canonical_key, placeholder in mapping.items():
        value = answers.get(canonical_key, "")
        # helpful default for front-page emails
        if canonical_key == "front_page_email_addresses" and not value:
            a = answers.get("party_a_email", "")
            b = answers.get("party_b_email", "")
            value = " / ".join([v for v in [a, b] if v])
        replacements[placeholder] = str(value)
    return replacements


def fill_placeholders(text: str, replacements: Dict[str, str]) -> str:
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def find_unresolved_placeholders(text: str) -> List[str]:
    return sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", text)))


def assemble_contract(answers: Dict[str, str], contract_type: str = "NDA", use_rag: bool = False) -> Tuple[str, List[str], List[dict]]:
    """
    Assemble a contract from answers.

    Args:
        answers: Canonical field -> value mapping (must include the subtype field).
        contract_type: Top-level contract type from the registry (default "NDA").
        use_rag: If True, use RAG to select best clause variants. Default False.

    Returns:
        (contract_text, unresolved_placeholders, selected_clauses)
    """
    library, order, placeholder_mappings, config = load_resources(contract_type)

    # Determine the subtype (e.g., "Mutual" or "Unilateral" for NDAs)
    subtype_field = config.get("subtype_field", "nda_type")
    subtype = answers[subtype_field]

    ordered_clause_names = order[subtype]

    rag_metadata = None
    if use_rag:
        # Import from same directory
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from clause_rag import select_clauses_rag
        clauses, rag_metadata = select_clauses_rag(
            contract_type, subtype, subtype_field, ordered_clause_names, answers
        )
    else:
        clauses = select_clauses(library, subtype, subtype_field, ordered_clause_names)

    replacements = build_replacements(subtype, answers, placeholder_mappings)

    rendered_clauses = []
    for clause in clauses:
        rendered = fill_placeholders(clause["text"], replacements)
        rendered_clauses.append(rendered.strip())

    contract_text = "\n\n".join(rendered_clauses).strip() + "\n"
    unresolved = find_unresolved_placeholders(contract_text)
    if use_rag and rag_metadata:
        return contract_text, unresolved, clauses, rag_metadata
    return contract_text, unresolved, clauses


if __name__ == "__main__":
    demo_answers = {
        "nda_type": "Unilateral",
        "party_a_name": "Acme Corp.",
        "party_a_entity_details": "Delaware corporation",
        "party_a_email": "legal@acme.com",
        "party_b_name": "Beta Ventures LLC",
        "party_b_entity_details": "Connecticut LLC",
        "party_b_email": "ops@betaventures.com",
        "purpose": "evaluating a potential partnership",
        "confidentiality_period_number": "3",
        "confidentiality_period_unit": "years",
        "governing_law": "Connecticut",
        "dispute_resolution_method": "Litigation",
        "front_page_email_addresses": "legal@acme.com / ops@betaventures.com",
        "special_provisions": ""
    }
    contract_text, unresolved, clauses = assemble_contract(demo_answers)
    print(contract_text)
    print("\nUnresolved placeholders:", unresolved)
