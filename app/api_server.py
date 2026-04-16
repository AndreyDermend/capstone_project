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
from contract_artifact import generate_artifact_html

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

# Ordered from most-specific phrase to single-word hint. First match wins.
# Full phrases like "service agreement" must appear before single-word keywords
# so we don't misclassify a service-agreement request just because the user
# happens to mention e.g. an "employee" of one of the parties.
CONTRACT_TYPE_RULES: List[Tuple[str, str]] = [
    ("service agreement",         "ServiceAgreement"),
    ("services agreement",        "ServiceAgreement"),
    ("non-disclosure agreement",  "NDA"),
    ("nondisclosure agreement",   "NDA"),
    ("confidentiality agreement", "NDA"),
    ("consulting agreement",      "ConsultingAgreement"),
    ("consultancy agreement",     "ConsultingAgreement"),
    ("employment agreement",      "EmploymentAgreement"),
    ("employment contract",       "EmploymentAgreement"),
    # Single-word hints — only used if no phrase matched above.
    ("nda",            "NDA"),
    ("non-disclosure", "NDA"),
    ("confidentiality", "NDA"),
    ("consulting",     "ConsultingAgreement"),
    ("consultant",     "ConsultingAgreement"),
    ("employment",     "EmploymentAgreement"),
    ("hiring",         "EmploymentAgreement"),
    ("employee",       "EmploymentAgreement"),
    ("contractor",     "ServiceAgreement"),
    ("cleaning",       "ServiceAgreement"),
    ("landscaping",    "ServiceAgreement"),
    ("maintenance",    "ServiceAgreement"),
    ("services",       "ServiceAgreement"),
]

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
    """Guess contract type from user text. Returns None if ambiguous.

    Matches longest/most-specific phrase first. See ``CONTRACT_TYPE_RULES``.
    """
    lower = text.lower()
    for keyword, ctype in CONTRACT_TYPE_RULES:
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


# Server-side session store. Keyed by a hash of the first user message so
# the same conversation (which Open WebUI re-sends in full every request)
# resolves to the same session. Prior approach embedded state as an HTML
# comment in the chat; Open WebUI's markdown renderer escaped the comment
# and leaked the whole state blob to the user.
import hashlib

_SESSION_STATES: Dict[str, dict] = {}


def _session_id(first_user_message: str) -> str:
    return hashlib.sha256(first_user_message.strip().encode("utf-8")).hexdigest()[:16]


def _new_state(initial_prompt: str, contract_type: Optional[str]) -> dict:
    return {
        "contract_type": contract_type,
        "verified_answers": {},
        "verified_evidence": {},
        "pending_follow_ups": [],
        "phase": "initial",
        "initial_prompt": initial_prompt,
    }


def get_or_create_session(messages: List[dict]) -> Tuple[Optional[str], Optional[dict]]:
    """Return (session_id, state) for this conversation, creating a fresh
    state dict on first sight. Returns (None, None) if there's no user
    message to key on yet."""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return None, None

    first = user_msgs[0].get("content", "").strip()
    if not first:
        return None, None

    sid = _session_id(first)
    state = _SESSION_STATES.get(sid)
    if state is None:
        state = _new_state(first, detect_contract_type(first))
        _SESSION_STATES[sid] = state
    return sid, state


def assemble_and_generate_docx(
    verified_answers: dict,
    evidence: dict,
    contract_type: str,
    label: str,
    port: int = 8001,
) -> Tuple[str, dict]:
    """Assemble contract, generate the HTML artifact + DOCX.

    Returns (chat_payload, final_answers) where chat_payload is the full
    agent message: an ```html artifact block followed by a single-line
    DOCX download link. Nothing else.
    """
    final = add_derived_defaults(dict(verified_answers), contract_type)
    result = assemble_contract(final, contract_type, use_rag=True)
    contract_text, unresolved, clauses, rag_meta = (
        result if len(result) == 4 else (*result, None)
    )

    # DOCX + citation sidecar
    docx_path, _sidecar_path = generate_contract_docx(
        contract_text, clauses, rag_meta, final, evidence,
        contract_type, label,
    )
    docx_url = f"http://localhost:{port}/download/{docx_path.name}"

    # Self-contained interactive artifact (hover-to-cite)
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
    )

    # Chat payload: DOCX link FIRST so it's clickable the instant the chunk
    # lands — then the artifact. Putting the link last meant waiting for the
    # whole ~30KB HTML block before the user could download.
    payload = (
        f"[\U0001F4C4 Download DOCX]({docx_url})\n\n"
        f"```html\n{artifact_html}\n```\n"
    )
    return payload, final


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
# Starlette's StreamingResponse iterates sync generators in a threadpool,
# so time.sleep here does NOT block the async event loop and other
# endpoints (like /download) can be served concurrently.
def stream_text(text: str, chunk_size: int = 40):
    """Yield SSE chunks for a block of text (typing animation)."""
    for i in range(0, len(text), chunk_size):
        yield make_chunk(text[i : i + chunk_size])
        time.sleep(0.005)


def emit_block(text: str):
    """Emit a pre-assembled block (e.g. the HTML artifact) as one SSE chunk.
    Skipping the 40-char / 5 ms typing throttle means a 30 KB artifact lands
    in a single event instead of ~3.75 s of dribbling — the DOCX link above
    it is clickable immediately."""
    yield make_chunk(text)


def stream_response(text: str):
    """Full streaming response with stop."""
    yield from stream_text(text)
    yield make_chunk("", finish_reason="stop")
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Pipeline logic
# ---------------------------------------------------------------------------
def handle_initial_prompt(state: dict, stream: bool):
    """Handle the first user message — extraction + follow-up generation.

    Mutates ``state`` (the session dict) in place with the extraction
    results; nothing about that state is written to the chat output.
    """
    prompt = state["initial_prompt"]
    contract_type = state["contract_type"]
    label = CONTRACT_LABELS.get(contract_type, contract_type)

    def generate():
        yield from stream_text(f"Drafting your **{label}**\u2026\n\n")

        extraction = extract_answers_from_prompt(prompt, contract_type)
        verified_answers, follow_ups, evidence = verify_and_prepare(
            extraction, contract_type
        )
        state["verified_answers"] = dict(verified_answers)
        state["verified_evidence"] = dict(evidence)

        if not follow_ups:
            state["phase"] = "complete"
            state["pending_follow_ups"] = []
            payload, final_answers = assemble_and_generate_docx(
                verified_answers, evidence, contract_type, label
            )
            state["verified_answers"] = final_answers
            yield from emit_block(payload)
        else:
            state["phase"] = "follow_up"
            state["pending_follow_ups"] = follow_ups
            n = len(follow_ups)
            yield from stream_text(
                f"I need **{n} more field{'s' if n != 1 else ''}** to finish. "
                "Please answer:\n\n"
            )
            for i, item in enumerate(follow_ups, 1):
                yield from stream_text(f"**{i}.** {item['question']}\n\n")
            yield from stream_text(
                "*Answer all at once — numbered, labeled, or one per line.*"
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
    """Handle follow-up answers — merge answers, assemble if complete.

    Mutates ``state`` in place. Nothing about state is ever written to
    the chat output.
    """
    contract_type = state["contract_type"]
    verified_answers = state["verified_answers"]
    verified_evidence = state.get("verified_evidence") or {}
    pending = state["pending_follow_ups"]
    label = CONTRACT_LABELS.get(contract_type, contract_type)

    new_answers = parse_follow_up_answers(user_text, pending, contract_type)
    verified_answers.update(new_answers)
    for fname in new_answers:
        verified_evidence.setdefault(fname, user_text.strip())

    still_missing = [item for item in pending if item["field"] not in verified_answers]
    state["pending_follow_ups"] = still_missing
    state["verified_evidence"] = verified_evidence

    def generate():
        if still_missing:
            state["phase"] = "follow_up"
            n = len(still_missing)
            yield from stream_text(
                f"I still need **{n} more field{'s' if n != 1 else ''}**:\n\n"
            )
            for i, item in enumerate(still_missing, 1):
                yield from stream_text(f"**{i}.** {item['question']}\n\n")
        else:
            state["phase"] = "complete"
            payload, final_answers = assemble_and_generate_docx(
                verified_answers, verified_evidence, contract_type, label
            )
            state["verified_answers"] = final_answers
            yield from emit_block(payload)

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

    conversation = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not conversation:
        text = (
            "Welcome to **LexiAgent**! Tell me what contract you need.\n\n"
            "I support:\n- Non-Disclosure Agreements (NDA)\n- Consulting Agreements\n"
            "- Employment Agreements\n- Service Agreements\n\n"
            "Just describe what you need in plain English."
        )
        if stream:
            return StreamingResponse(stream_response(text), media_type="text/event-stream")
        return JSONResponse(make_response(text))

    sid, state = get_or_create_session(conversation)
    user_messages = [m for m in conversation if m["role"] == "user"]
    latest_user = user_messages[-1]["content"] if user_messages else ""

    if state is None or state["contract_type"] is None:
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

    phase = state["phase"]
    if phase == "initial":
        return handle_initial_prompt(state, stream)
    if phase == "follow_up":
        return handle_follow_up(state, latest_user, stream)
    # phase == "complete"
    text = (
        "Your contract has been generated \u2014 check the download link above.\n\n"
        "To draft a new contract, start a **new chat** so I know to reset."
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
