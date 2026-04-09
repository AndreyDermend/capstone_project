"""
LexiAgent API Server — OpenAI-compatible endpoint for Open WebUI integration.

Wraps the full LexiAgent pipeline (extraction → follow-ups → RAG assembly)
behind an /v1/chat/completions endpoint so Open WebUI can talk to it
as if it were any OpenAI model.

Usage:
    python app/api_server.py          # starts on port 8001
    python app/api_server.py --port 9000
"""

import json
import sys
import time
import uuid
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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

OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="LexiAgent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONTRACT_TYPES = {
    "nda": "NDA",
    "non-disclosure": "NDA",
    "confidentiality": "NDA",
    "consulting": "ConsultingAgreement",
    "consultant": "ConsultingAgreement",
    "employment": "EmploymentAgreement",
    "employee": "EmploymentAgreement",
    "hiring": "EmploymentAgreement",
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
# Helpers
# ---------------------------------------------------------------------------
def detect_contract_type(text: str) -> Optional[str]:
    """Guess contract type from user text. Returns None if ambiguous."""
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
    """Parse free-form follow-up answers into field values."""
    lookup = field_lookup(contract_type)
    answers: Dict[str, Any] = {}

    # Try numbered answers first: "1. some value" or "1) some value"
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

    # Try "field_label: value" pattern
    for item in pending_fields:
        fname = item["field"]
        field = lookup.get(fname)
        if not field:
            continue
        label = field.get("label", fname).lower()
        # Match "Label: value" or "label - value"
        pattern = re.compile(
            rf"(?:^|\n)\s*{re.escape(label)}\s*[:=\-]\s*(.+)",
            re.IGNORECASE,
        )
        m = pattern.search(user_text)
        if m:
            norm, ok = normalize_value_for_field(m.group(1).strip(), field)
            if ok:
                answers[fname] = norm

    # If we got less than half, try line-by-line assignment
    if len(answers) < len(pending_fields) * 0.5:
        lines = [l.strip() for l in user_text.strip().split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if i < len(pending_fields):
                fname = pending_fields[i]["field"]
                field = lookup.get(fname)
                if field and fname not in answers:
                    # Strip any leading "label:" prefix
                    val = re.sub(r"^[^:]+:\s*", "", line) if ":" in line else line
                    norm, ok = normalize_value_for_field(val.strip(), field)
                    if ok:
                        answers[fname] = norm

    return answers


def extract_state_from_history(messages: List[dict]) -> dict:
    """Parse conversation history to reconstruct pipeline state."""
    state = {
        "contract_type": None,
        "verified_answers": {},
        "pending_follow_ups": [],
        "phase": "initial",  # initial | follow_up | complete
        "initial_prompt": None,
    }

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user" and state["initial_prompt"] is None:
            state["initial_prompt"] = content
            state["contract_type"] = detect_contract_type(content)

        # Parse our own assistant messages to recover state
        if role == "assistant":
            # Look for the JSON state block we embed
            m = re.search(
                r"<!--LEXI_STATE(.*?)-->",
                content,
                re.DOTALL,
            )
            if m:
                try:
                    saved = json.loads(m.group(1))
                    state["verified_answers"] = saved.get("verified_answers", {})
                    state["pending_follow_ups"] = saved.get("pending_follow_ups", [])
                    state["contract_type"] = saved.get("contract_type", state["contract_type"])
                    state["phase"] = saved.get("phase", "follow_up")
                except json.JSONDecodeError:
                    pass

        # If user message comes after a follow_up phase, it's follow-up answers
        if role == "user" and state["phase"] == "follow_up" and content != state["initial_prompt"]:
            ct = state["contract_type"] or "NDA"
            new_answers = parse_follow_up_answers(
                content, state["pending_follow_ups"], ct
            )
            state["verified_answers"].update(new_answers)

    return state


def assemble_and_generate_docx(
    verified_answers: dict,
    evidence: dict,
    contract_type: str,
    label: str,
    port: int = 8001,
) -> Tuple[str, str]:
    """Assemble contract, generate DOCX, return (summary_markdown, download_url)."""
    final = add_derived_defaults(dict(verified_answers), contract_type)
    result = assemble_contract(final, contract_type, use_rag=True)
    contract_text, unresolved, clauses, rag_meta = (
        result if len(result) == 4 else (*result, None)
    )

    # Generate DOCX
    docx_path = generate_contract_docx(
        contract_text, clauses, rag_meta, final, evidence,
        contract_type, label,
    )
    filename = docx_path.name
    download_url = f"http://localhost:{port}/download/{filename}"

    # Build summary
    n_clauses = len(clauses)
    sources = sorted(set(c.get("source", "?") for c in clauses))
    rag_count = sum(1 for m in (rag_meta or {}).values() if m.get("method") == "rag")

    summary = (
        f"Your **{label}** is ready!\n\n"
        f"### Assembly Summary\n\n"
        f"- **{n_clauses} clauses** from {len(sources)} templates ({', '.join(sources)})\n"
        f"- **{rag_count}** selected via RAG semantic matching\n"
        f"- **{n_clauses - rag_count}** selected deterministically\n\n"
        f"### Download\n\n"
        f"**[Download {label} (DOCX)]({download_url})**\n\n"
        f"The document includes:\n"
        f"- Professional formatting (Times New Roman)\n"
        f"- Blue highlighted user-provided values with field markers\n"
        f"- Source citation on every clause (template, variant, RAG score)\n"
        f"- Full audit appendix with clause sources + extraction evidence\n"
    )

    if unresolved:
        summary += f"\n**Warning:** {len(unresolved)} unresolved placeholders: {', '.join(unresolved)}\n"

    return summary, final


def embed_state(
    contract_type: str,
    verified_answers: dict,
    pending_follow_ups: list,
    phase: str,
) -> str:
    """Embed state as a hidden HTML comment for conversation continuity."""
    state = {
        "contract_type": contract_type,
        "verified_answers": verified_answers,
        "pending_follow_ups": pending_follow_ups,
        "phase": phase,
    }
    # Hidden from rendering in Open WebUI but parseable by us
    return f"<!--LEXI_STATE{json.dumps(state)}-->"


# ---------------------------------------------------------------------------
# Response formatting (OpenAI-compatible SSE)
# ---------------------------------------------------------------------------
def make_chunk(content: str, finish_reason: str = None) -> str:
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "lexiagent",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def make_response(content: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "lexiagent",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------
import asyncio

async def stream_text(text: str, chunk_size: int = 40):
    """Yield SSE chunks for a block of text."""
    for i in range(0, len(text), chunk_size):
        yield make_chunk(text[i : i + chunk_size])
        await asyncio.sleep(0.005)


async def stream_response(text: str):
    """Full streaming response with stop."""
    async for chunk in stream_text(text):
        yield chunk
    yield make_chunk("", finish_reason="stop")
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Pipeline logic
# ---------------------------------------------------------------------------
def handle_initial_prompt(prompt: str, contract_type: str, stream: bool):
    """Handle the first user message — extraction + follow-up generation."""
    label = CONTRACT_LABELS.get(contract_type, contract_type)

    def generate():
        # Status update
        yield from stream_text(f"**LexiAgent** — Drafting a **{label}**\n\n")
        yield from stream_text(f"Analyzing your request with AI extraction...\n\n")

        # Run extraction (this is the slow part — 30-120s)
        extraction = extract_answers_from_prompt(prompt, contract_type)
        verified_answers, follow_ups, evidence = verify_and_prepare(
            extraction, contract_type
        )

        # Show what was extracted
        yield from stream_text("### Extracted Fields\n\n")
        if verified_answers:
            yield from stream_text("| Field | Value |\n|-------|-------|\n")
            lookup = field_lookup(contract_type)
            for fname, val in verified_answers.items():
                field = lookup.get(fname, {})
                label_str = field.get("label", fname) if isinstance(field, dict) else fname
                yield from stream_text(f"| {label_str} | {val} |\n")
            yield from stream_text("\n")

        if not follow_ups:
            # All fields extracted — assemble immediately
            yield from stream_text("All required fields extracted. Assembling contract with RAG...\n\n")
            summary, final_answers = assemble_and_generate_docx(
                verified_answers, evidence, contract_type, label
            )
            yield from stream_text(summary)
            yield from stream_text("\n\n")
            yield from stream_text(
                embed_state(contract_type, final_answers, [], "complete")
            )
        else:
            # Need follow-ups
            n = len(follow_ups)
            yield from stream_text(
                f"I still need **{n} more field{'s' if n != 1 else ''}** to complete your contract. "
                f"Please answer the following:\n\n"
            )
            for i, item in enumerate(follow_ups, 1):
                yield from stream_text(f"**{i}.** {item['question']}\n\n")

            yield from stream_text(
                "\n*You can answer all at once — numbered, labeled, or one per line.*\n\n"
            )
            yield from stream_text(
                embed_state(contract_type, verified_answers, follow_ups, "follow_up")
            )

        yield make_chunk("", finish_reason="stop")
        yield "data: [DONE]\n\n"

    if stream:
        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        # Collect full response (non-streaming fallback)
        full = []
        for chunk_str in generate():
            if chunk_str.startswith("data: {"):
                try:
                    d = json.loads(chunk_str[6:])
                    c = d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    full.append(c)
                except:
                    pass
        return JSONResponse(make_response("".join(full)))


def handle_follow_up(state: dict, user_text: str, stream: bool):
    """Handle follow-up answers — merge answers, assemble if complete."""
    contract_type = state["contract_type"]
    verified_answers = state["verified_answers"]
    pending = state["pending_follow_ups"]
    label = CONTRACT_LABELS.get(contract_type, contract_type)

    # Parse the user's answers
    new_answers = parse_follow_up_answers(user_text, pending, contract_type)
    verified_answers.update(new_answers)

    # Check what's still missing
    still_missing = []
    for item in pending:
        if item["field"] not in verified_answers:
            still_missing.append(item)

    def generate():
        if new_answers:
            yield from stream_text("### Additional Fields Collected\n\n")
            yield from stream_text("| Field | Value |\n|-------|-------|\n")
            lookup = field_lookup(contract_type)
            for fname, val in new_answers.items():
                field = lookup.get(fname, {})
                label_str = field.get("label", fname) if isinstance(field, dict) else fname
                yield from stream_text(f"| {label_str} | {val} |\n")
            yield from stream_text("\n")

        if still_missing:
            n = len(still_missing)
            yield from stream_text(
                f"I still need **{n} more field{'s' if n != 1 else ''}**:\n\n"
            )
            for i, item in enumerate(still_missing, 1):
                yield from stream_text(f"**{i}.** {item['question']}\n\n")
            yield from stream_text(
                embed_state(contract_type, verified_answers, still_missing, "follow_up")
            )
        else:
            # All fields collected — assemble
            yield from stream_text("All fields collected! Assembling your contract with RAG clause selection...\n\n")
            summary, final_answers = assemble_and_generate_docx(
                verified_answers, {}, contract_type, label
            )
            yield from stream_text(summary)
            yield from stream_text("\n\n")
            yield from stream_text(
                embed_state(contract_type, final_answers, [], "complete")
            )

        yield make_chunk("", finish_reason="stop")
        yield "data: [DONE]\n\n"

    if stream:
        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        full = []
        for chunk_str in generate():
            if chunk_str.startswith("data: {"):
                try:
                    d = json.loads(chunk_str[6:])
                    c = d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    full.append(c)
                except:
                    pass
        return JSONResponse(make_response("".join(full)))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "lexiagent",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "lexiagent",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    # Filter to user/assistant messages only
    conversation = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not conversation:
        text = "Welcome to **LexiAgent**! Tell me what contract you need.\n\nI support:\n- Non-Disclosure Agreements (NDA)\n- Consulting Agreements\n- Employment Agreements\n- Service Agreements\n\nJust describe what you need in plain English."
        if stream:
            return StreamingResponse(stream_response(text), media_type="text/event-stream")
        return JSONResponse(make_response(text))

    # Extract state from conversation
    state = extract_state_from_history(conversation)
    user_messages = [m for m in conversation if m["role"] == "user"]
    latest_user = user_messages[-1]["content"] if user_messages else ""

    # If no contract type detected, ask
    if state["contract_type"] is None:
        text = (
            "I couldn't determine the contract type from your request. "
            "Which type do you need?\n\n"
            "1. **NDA** (Non-Disclosure Agreement)\n"
            "2. **Consulting Agreement**\n"
            "3. **Employment Agreement**\n"
            "4. **Service Agreement**\n\n"
            "Just tell me the number or name, or rephrase your request with more detail."
        )
        if stream:
            return StreamingResponse(stream_response(text), media_type="text/event-stream")
        return JSONResponse(make_response(text))

    # Route based on phase
    if state["phase"] == "initial" or (state["phase"] == "follow_up" and not state["pending_follow_ups"]):
        # First extraction
        return handle_initial_prompt(
            state["initial_prompt"], state["contract_type"], stream
        )
    elif state["phase"] == "follow_up":
        return handle_follow_up(state, latest_user, stream)
    elif state["phase"] == "complete":
        # Contract already assembled — offer to start a new one
        text = (
            "Your contract has been generated! Check the download link above.\n\n"
            "To draft a new contract, just describe what you need."
        )
        if stream:
            return StreamingResponse(stream_response(text), media_type="text/event-stream")
        return JSONResponse(make_response(text))


# ---------------------------------------------------------------------------
# File download — runs in threadpool to avoid blocking during SSE streams
# ---------------------------------------------------------------------------
@app.get("/download/{filename}")
async def download_file(filename: str):
    filepath = OUTPUT_DIR / filename
    if not filepath.exists() or not filename.endswith(".docx"):
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "model": "lexiagent"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="LexiAgent API Server")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    print(f"\n  LexiAgent API Server")
    print(f"  Listening on http://{args.host}:{args.port}")
    print(f"  OpenAI-compatible endpoint: http://localhost:{args.port}/v1")
    print(f"  Add this URL as a connection in Open WebUI\n")

    uvicorn.run(app, host=args.host, port=args.port)
