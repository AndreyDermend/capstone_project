"""
LexiAgent Web UI — Gradio chat interface for demo showcases.

Provides a ChatGPT-like interface where users describe what contract they need
and LexiAgent extracts fields, asks follow-ups, and assembles the contract
as a downloadable DOCX with embedded source citations.

Usage:
    python3 app/web_ui.py              # opens at http://localhost:7860
    python3 app/web_ui.py --port 3000  # custom port
    python3 app/web_ui.py --share      # public URL for remote demos
"""

import json
import re
import sys
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import gradio as gr

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from run_intake_loop import (
    extract_answers_from_prompt,
    verify_and_prepare,
    add_derived_defaults,
    required_fields,
    field_lookup,
    schema_fields,
    field_name as get_field_name,
    normalize_value_for_field,
)
from assemble_contract import assemble_contract, load_resources
from contract_docx import generate_contract_docx
from contract_artifact import generate_artifact_html

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONTRACT_TYPES = {
    "nda": "NDA",
    "non-disclosure": "NDA",
    "confidentiality": "NDA",
    "consulting": "ConsultingAgreement",
    "consultant": "ConsultingAgreement",
    "employment": "EmploymentAgreement",
    "employee": "EmploymentAgreement",
    "hiring": "EmploymentAgreement",
    "hire": "EmploymentAgreement",
    "service": "ServiceAgreement",
    "services": "ServiceAgreement",
    "contractor": "ServiceAgreement",
    "cleaning": "ServiceAgreement",
    "landscaping": "ServiceAgreement",
    "maintenance": "ServiceAgreement",
}

CONTRACT_LABELS = {
    "NDA": "Non-Disclosure Agreement",
    "ConsultingAgreement": "Consulting Agreement",
    "EmploymentAgreement": "Employment Agreement",
    "ServiceAgreement": "Service Agreement",
}


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
class SessionState:
    def __init__(self):
        self.contract_type: Optional[str] = None
        self.verified_answers: Dict[str, Any] = {}
        self.verified_evidence: Dict[str, str] = {}
        self.pending_follow_ups: List[dict] = []
        self.phase: str = "initial"  # initial | follow_up | complete
        self.initial_prompt: Optional[str] = None
        self.docx_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def detect_contract_type(text: str) -> Optional[str]:
    lower = text.lower()
    for keyword, ctype in CONTRACT_TYPES.items():
        if keyword in lower:
            return ctype
    return None


def parse_follow_up_answers(
    user_text: str,
    pending_fields: List[dict],
    contract_type: str,
) -> Dict[str, Any]:
    lookup = field_lookup(contract_type)
    answers: Dict[str, Any] = {}

    # Try numbered answers: "1. value" or "1) value"
    numbered = re.findall(r"(?:^|\n)\s*(\d+)[.)]\s*(.+)", user_text)
    if numbered and len(numbered) >= len(pending_fields) * 0.5:
        for num_str, value in numbered:
            idx = int(num_str) - 1
            if 0 <= idx < len(pending_fields):
                fname = pending_fields[idx]["field"]
                field = lookup.get(fname)
                if field:
                    norm, ok = normalize_value_for_field(value.strip(), field)
                    if ok:
                        answers[fname] = norm
        return answers

    # Try "label: value" pattern
    for item in pending_fields:
        fname = item["field"]
        field = lookup.get(fname)
        if not field:
            continue
        label = field.get("label", fname).lower()
        pattern = re.compile(
            rf"(?:^|\n)\s*{re.escape(label)}\s*[:=\-]\s*(.+)",
            re.IGNORECASE,
        )
        m = pattern.search(user_text)
        if m:
            norm, ok = normalize_value_for_field(m.group(1).strip(), field)
            if ok:
                answers[fname] = norm

    # Line-by-line fallback
    if len(answers) < len(pending_fields) * 0.5:
        lines = [l.strip() for l in user_text.strip().split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if i < len(pending_fields):
                fname = pending_fields[i]["field"]
                field = lookup.get(fname)
                if field and fname not in answers:
                    val = re.sub(r"^[^:]+:\s*", "", line) if ":" in line else line
                    norm, ok = normalize_value_for_field(val.strip(), field)
                    if ok:
                        answers[fname] = norm

    return answers


def format_extracted_table(verified_answers: dict, contract_type: str) -> str:
    lines = ["| Field | Value |", "|-------|-------|"]
    lookup = field_lookup(contract_type)
    for fname, val in verified_answers.items():
        field = lookup.get(fname, {})
        label = field.get("label", fname) if isinstance(field, dict) else fname
        lines.append(f"| {label} | {val} |")
    return "\n".join(lines)


def assemble_and_generate_docx(
    verified_answers: dict,
    evidence: dict,
    contract_type: str,
    label: str,
) -> Tuple[str, Optional[str]]:
    """Assemble contract and generate DOCX. Returns (summary_text, docx_path)."""
    final = add_derived_defaults(dict(verified_answers), contract_type)
    result = assemble_contract(final, contract_type, use_rag=True)
    contract_text, unresolved, clauses, rag_meta = (
        result if len(result) == 4 else (*result, None)
    )

    # Generate clean DOCX + citation JSON sidecar
    docx_path, _sidecar_path = generate_contract_docx(
        contract_text, clauses, rag_meta, final, evidence,
        contract_type, label,
    )

    # Generate the interactive HTML artifact next to the DOCX so the user
    # can open it in a browser tab (Gradio can't render it inline like
    # Open WebUI does, so we serve it as a file).
    _, _, placeholder_mappings, cfg = load_resources(contract_type)
    subtype_field = cfg.get("subtype_field", "nda_type")
    subtype = final.get(subtype_field, next(iter(placeholder_mappings.keys())))
    artifact_html = generate_artifact_html(
        clauses=clauses,
        rag_metadata=rag_meta,
        verified_answers=final,
        evidence=evidence,
        placeholder_mappings=placeholder_mappings,
        subtype=subtype,
        contract_type=contract_type,
        label=label,
        docx_bytes=docx_path.read_bytes(),
        docx_filename=docx_path.name,
    )
    artifact_path = docx_path.with_suffix(".html")
    artifact_path.write_text(artifact_html)

    summary = (
        f"Your **{label}** is ready.\n\n"
        f"Open the interactive view (hover any clause or highlighted value "
        f"for its source): `{artifact_path}`\n"
    )
    return summary, str(docx_path), final


# ---------------------------------------------------------------------------
# Chat handler — returns (response, state, docx_path_or_none)
# ---------------------------------------------------------------------------
def respond(message: str, history: list, state: dict) -> Tuple[str, dict, Optional[str]]:
    """Process a user message and return (response, updated_state, docx_path)."""

    ss = SessionState()
    if state:
        ss.contract_type = state.get("contract_type")
        ss.verified_answers = state.get("verified_answers", {})
        ss.verified_evidence = state.get("verified_evidence", {})
        ss.pending_follow_ups = state.get("pending_follow_ups", [])
        ss.phase = state.get("phase", "initial")
        ss.initial_prompt = state.get("initial_prompt")
        ss.docx_path = state.get("docx_path")

    # --- PHASE: Initial ---
    if ss.phase == "initial":
        ss.initial_prompt = message
        ss.contract_type = detect_contract_type(message)

        if ss.contract_type is None:
            response = (
                "I couldn't determine the contract type. Which do you need?\n\n"
                "1. **NDA** (Non-Disclosure Agreement)\n"
                "2. **Consulting Agreement**\n"
                "3. **Employment Agreement**\n"
                "4. **Service Agreement**\n\n"
                "Type the number or name, or rephrase with more detail."
            )
            return response, vars(ss), None

        label = CONTRACT_LABELS.get(ss.contract_type, ss.contract_type)
        parts = [f"**LexiAgent** — Drafting a **{label}**\n"]
        parts.append("Analyzing your request with AI extraction...\n")

        # Run extraction
        extraction = extract_answers_from_prompt(message, ss.contract_type)
        verified, follow_ups, evidence = verify_and_prepare(extraction, ss.contract_type)
        ss.verified_answers = verified
        ss.verified_evidence = evidence

        # Show extracted fields
        if verified:
            parts.append("### Extracted Fields\n")
            parts.append(format_extracted_table(verified, ss.contract_type))
            parts.append("")

        if not follow_ups:
            # Assemble immediately
            parts.append("All required fields extracted! Assembling contract with RAG...\n")
            summary, docx_path, final = assemble_and_generate_docx(
                verified, evidence, ss.contract_type, label
            )
            ss.verified_answers = final
            ss.phase = "complete"
            ss.docx_path = docx_path
            parts.append(summary)
            return "\n".join(parts), vars(ss), docx_path
        else:
            ss.pending_follow_ups = follow_ups
            ss.phase = "follow_up"
            n = len(follow_ups)
            parts.append(
                f"I need **{n} more field{'s' if n != 1 else ''}** to complete your contract:\n"
            )
            for i, item in enumerate(follow_ups, 1):
                parts.append(f"**{i}.** {item['question']}\n")
            parts.append("\n*Answer all at once — numbered, labeled, or one per line.*")

        return "\n".join(parts), vars(ss), None

    # --- PHASE: Follow-up ---
    elif ss.phase == "follow_up":
        if not ss.contract_type:
            ct = detect_contract_type(message)
            num_map = {"1": "NDA", "2": "ConsultingAgreement", "3": "EmploymentAgreement", "4": "ServiceAgreement"}
            ct = ct or num_map.get(message.strip())
            if ct:
                ss.contract_type = ct
                ss.phase = "initial"
                return respond(ss.initial_prompt or message, history, vars(ss))
            return "Please pick a contract type (1-4) or describe your need.", vars(ss), None

        # Parse follow-up answers
        new_answers = parse_follow_up_answers(message, ss.pending_follow_ups, ss.contract_type)
        ss.verified_answers.update(new_answers)

        # Add follow-up evidence (the user's raw text for each answered field)
        for fname, val in new_answers.items():
            ss.verified_evidence[fname] = f"Follow-up answer: \"{val}\""

        still_missing = [
            item for item in ss.pending_follow_ups
            if item["field"] not in ss.verified_answers
        ]

        parts = []
        label = CONTRACT_LABELS.get(ss.contract_type, ss.contract_type)

        if new_answers:
            parts.append("### Additional Fields Collected\n")
            parts.append(format_extracted_table(new_answers, ss.contract_type))
            parts.append("")

        if still_missing:
            ss.pending_follow_ups = still_missing
            n = len(still_missing)
            parts.append(f"I still need **{n} more field{'s' if n != 1 else ''}**:\n")
            for i, item in enumerate(still_missing, 1):
                parts.append(f"**{i}.** {item['question']}\n")
            return "\n".join(parts), vars(ss), None
        else:
            parts.append("All fields collected! Assembling your contract with RAG...\n")
            summary, docx_path, final = assemble_and_generate_docx(
                ss.verified_answers, ss.verified_evidence, ss.contract_type, label
            )
            ss.verified_answers = final
            ss.phase = "complete"
            ss.docx_path = docx_path
            parts.append(summary)
            return "\n".join(parts), vars(ss), docx_path

    # --- PHASE: Complete ---
    elif ss.phase == "complete":
        ct = detect_contract_type(message)
        if ct:
            ss2 = SessionState()
            ss2.initial_prompt = message
            ss2.contract_type = ct
            return respond(message, history, vars(ss2))

        return (
            "Your contract is ready! Use the download button below.\n\n"
            "To draft a new contract, just describe what you need."
        ), vars(ss), ss.docx_path

    return "Something went wrong. Please start a new conversation.", vars(ss), None


# ---------------------------------------------------------------------------
# Gradio interface
# ---------------------------------------------------------------------------
def create_ui():
    with gr.Blocks(
        title="LexiAgent",
        theme=gr.themes.Soft(
            primary_hue="blue",
            neutral_hue="gray",
        ),
        css="""
        .contain { max-width: 900px; margin: auto; }
        footer { display: none !important; }
        .download-box { margin-top: 10px; }
        """,
    ) as demo:
        gr.Markdown(
            """
            # LexiAgent
            **Deterministic Contract Drafting with AI Extraction & RAG Clause Selection**

            Describe the contract you need in plain English. LexiAgent will extract the details,
            ask follow-up questions for anything missing, then assemble a professional DOCX contract
            from vetted legal templates — with full source citations embedded in the document.

            *Supported: NDA, Consulting Agreement, Employment Agreement, Service Agreement*
            """
        )

        state = gr.State(value={})
        chatbot = gr.Chatbot(
            height=500,
            show_label=False,
            render_markdown=True,
        )
        docx_output = gr.File(
            label="Download Contract",
            visible=False,
            elem_classes=["download-box"],
        )
        msg = gr.Textbox(
            placeholder="Describe the contract you need...",
            show_label=False,
            container=False,
            scale=7,
        )
        with gr.Row():
            submit_btn = gr.Button("Send", variant="primary", scale=1)
            clear_btn = gr.Button("New Contract", scale=1)

        def user_submit(message, history, state):
            if not message.strip():
                return "", history, state
            history = history + [[message, None]]
            return "", history, state

        def bot_respond(history, state):
            user_msg = history[-1][0]
            response, new_state, docx_path = respond(user_msg, history[:-1], state)
            history[-1][1] = response

            if docx_path:
                return (
                    history,
                    new_state,
                    gr.update(value=docx_path, visible=True),
                )
            return history, new_state, gr.update(visible=False)

        def clear_chat():
            return [], {}, gr.update(value=None, visible=False)

        msg.submit(
            user_submit, [msg, chatbot, state], [msg, chatbot, state]
        ).then(
            bot_respond, [chatbot, state], [chatbot, state, docx_output]
        )
        submit_btn.click(
            user_submit, [msg, chatbot, state], [msg, chatbot, state]
        ).then(
            bot_respond, [chatbot, state], [chatbot, state, docx_output]
        )
        clear_btn.click(clear_chat, outputs=[chatbot, state, docx_output])

    return demo


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LexiAgent Web UI")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true", help="Create public URL")
    args = parser.parse_args()

    demo = create_ui()
    print(f"\n  LexiAgent Web UI")
    print(f"  http://localhost:{args.port}")
    if args.share:
        print(f"  (public sharing URL will appear below)\n")
    else:
        print(f"  Add --share for a public URL\n")

    demo.launch(
        server_port=args.port,
        share=args.share,
        show_error=True,
    )
