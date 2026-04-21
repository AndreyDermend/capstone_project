"""
Smoke test: prove the Claude API provider works end-to-end.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python app/test_claude_provider.py
    # or run a specific contract type:
    python app/test_claude_provider.py ServiceAgreement

This script forces LLM_PROVIDER=anthropic, runs extraction on a representative
prompt, and verifies:
  1. The Claude tool call returns structured JSON matching the schema
  2. Extracted values have quoted evidence (evidence-gating still applies)
  3. The downstream verify_and_prepare → assemble_contract pipeline works
     unchanged — i.e., the Claude swap is transparent to the rest of the app

Exit code 0 on success, 1 on failure.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


TEST_PROMPTS = {
    "NDA": (
        "We need a unilateral NDA between Acme Corp, a Delaware corporation, "
        "and Beta Ventures LLC, a Connecticut LLC, to evaluate a potential "
        "partnership for 3 years under Connecticut law using arbitration. "
        "Acme's email is legal@acme.com and Beta's email is ops@beta.com."
    ),
    "ConsultingAgreement": (
        "Quantum Analytics Corp at 200 State St, Hartford CT 06103 is hiring "
        "James Morrison Consulting at 55 Elm St, New Haven CT 06510 to provide "
        "data analytics services starting January 15, 2026. Compensation is "
        "$15,000 paid milestone-based. 30 days termination notice. Connecticut "
        "law governs. Client owns all IP."
    ),
    "ServiceAgreement": (
        "GlobalTech Solutions Inc at 500 Market St, San Francisco CA 94105 and "
        "CleanPro Services LLC at 200 Oak Blvd, Oakland CA 94612. CleanPro will "
        "provide commercial office cleaning for $3,500 per month, paid monthly, "
        "starting February 1, 2026. 30 days notice to terminate. California law, "
        "mediation for disputes. Client owns all IP."
    ),
    "EmploymentAgreement": (
        "BrightPath Inc at 789 Pine St, Denver CO 80202 is hiring Sarah Johnson "
        "of 321 Elm St, Boulder CO 80301 as Marketing Director. Full-time, "
        "$145,000 per year, bi-weekly pay, starting March 1, 2026. 60 days "
        "notice to terminate. Colorado law governs."
    ),
}


def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("       export ANTHROPIC_API_KEY=sk-ant-... and retry.")
        return 1

    # Force the Claude path regardless of what .env says
    os.environ["LLM_PROVIDER"] = "anthropic"

    from run_intake_loop import (
        extract_answers_from_prompt,
        verify_and_prepare,
        add_derived_defaults,
    )
    from assemble_contract import assemble_contract

    target = sys.argv[1] if len(sys.argv) > 1 else "NDA"
    if target not in TEST_PROMPTS:
        print(f"Unknown contract type: {target}")
        print(f"Choose one of: {', '.join(TEST_PROMPTS)}")
        return 1

    prompt = TEST_PROMPTS[target]
    print(f"Provider:      anthropic (model={os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6')})")
    print(f"Contract type: {target}")
    print(f"Prompt:        {prompt}\n")

    started = time.time()
    extraction = extract_answers_from_prompt(prompt, target)
    elapsed = time.time() - started

    known = extraction.get("known_answers", {}) or {}
    evidence = extraction.get("field_evidence", {}) or {}
    follow_ups = extraction.get("follow_up_questions", []) or []

    print(f"Claude call returned in {elapsed:.2f}s")
    print(f"  extracted {len(known)} fields")
    print(f"  evidence entries: {len(evidence)}")
    print(f"  model-suggested follow-ups: {len(follow_ups)}\n")

    if not known:
        print("FAIL: Claude returned no extracted fields.")
        return 1

    missing_evidence = [k for k in known if not evidence.get(k)]
    if missing_evidence:
        print(f"WARN: {len(missing_evidence)} extracted fields lack evidence:")
        for k in missing_evidence:
            print(f"  - {k}")
        print("       verify_and_prepare() will drop these fields.\n")

    verified_answers, final_follow_ups, verified_evidence = verify_and_prepare(
        extraction, target
    )
    print(f"After verification:")
    print(f"  verified_answers: {len(verified_answers)}")
    print(f"  final_follow_ups: {len(final_follow_ups)}\n")

    if not verified_answers:
        print("FAIL: no fields survived verification (evidence-gating rejected everything).")
        return 1

    print("Sample verified fields with evidence:")
    for field_name in list(verified_answers)[:5]:
        value = verified_answers[field_name]
        cite = verified_evidence.get(field_name, "")
        print(f'  {field_name} = {value!r}')
        print(f'    ↳ evidence: "{cite}"')

    # Assembly sanity check: if all required fields are present, prove the
    # downstream pipeline is provider-agnostic.
    if not final_follow_ups:
        defaulted = add_derived_defaults(dict(verified_answers), target)
        contract_text, unresolved, _clauses = assemble_contract(defaulted, target)
        print(f"\nAssembly OK: {len(contract_text)} chars, {len(unresolved)} unresolved placeholders")
        if unresolved:
            print(f"  unresolved: {unresolved}")
            return 1
    else:
        print(f"\nSkipping assembly — {len(final_follow_ups)} required field(s) still need follow-ups.")

    print("\nPASS: Claude provider works end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
