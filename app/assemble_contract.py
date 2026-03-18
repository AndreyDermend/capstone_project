import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "clause_library"
CONFIG_DIR = ROOT / "config"

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def load_jsonl(path: Path) -> List[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def load_resources():
    library = load_jsonl(DATA_DIR / "master_clause_library.jsonl")
    order = load_json(CONFIG_DIR / "assembly_order.json")
    mappings = load_json(CONFIG_DIR / "placeholder_mappings.json")
    return library, order, mappings

def select_clauses(library: List[dict], nda_type: str, ordered_clause_names: List[str]) -> List[dict]:
    selected = []
    for clause_name in ordered_clause_names:
        candidates = [r for r in library if r["nda_type"] == nda_type and r["clause_name"] == clause_name]
        if candidates:
            candidates.sort(key=lambda r: r.get("sort_order", 999))
            selected.append(candidates[0])
    return selected

def build_replacements(nda_type: str, answers: Dict[str, str], placeholder_mappings: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    mapping = placeholder_mappings[nda_type]
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

def assemble_contract(answers: Dict[str, str]) -> Tuple[str, List[str], List[dict]]:
    library, order, placeholder_mappings = load_resources()
    nda_type = answers["nda_type"]
    ordered_clause_names = order[nda_type]
    clauses = select_clauses(library, nda_type, ordered_clause_names)
    replacements = build_replacements(nda_type, answers, placeholder_mappings)

    rendered_clauses = []
    for clause in clauses:
        rendered = fill_placeholders(clause["text"], replacements)
        rendered_clauses.append(rendered.strip())

    contract_text = "\n\n".join(rendered_clauses).strip() + "\n"
    unresolved = find_unresolved_placeholders(contract_text)
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
