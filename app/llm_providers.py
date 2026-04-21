"""
LLM provider abstraction for extraction.

One function, two backends. Select with LLM_PROVIDER:
  - "ollama"    (default) — local inference via host Ollama
  - "anthropic"           — Claude API (requires ANTHROPIC_API_KEY)

The function contract is identical in both paths: pass a system prompt, a
user prompt, and a JSON schema; get back a JSON string matching the schema.
Callers don't need to know which backend served the response.
"""

from __future__ import annotations

import json
import os
from typing import Optional


def extract_structured(
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    model: Optional[str] = None,
) -> str:
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if provider == "anthropic":
        return _extract_anthropic(system_prompt, user_prompt, schema)
    return _extract_ollama(system_prompt, user_prompt, schema, model)


def _extract_ollama(system_prompt: str, user_prompt: str, schema: dict, model: Optional[str]) -> str:
    from ollama import chat

    resolved_model = model or os.getenv("EXTRACTION_MODEL", "qwen3:4b")
    response = chat(
        model=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        format=schema,
        think=False,
    )
    return response.message.content


def _extract_anthropic(system_prompt: str, user_prompt: str, schema: dict) -> str:
    from anthropic import Anthropic

    client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    resolved_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # Claude enforces structured output via tool_choice. We declare a single
    # tool whose input_schema is the extraction schema and force the model
    # to call it — the tool_use block's `input` is the structured result.
    tool = {
        "name": "submit_extraction",
        "description": "Submit the extracted contract fields with evidence and any follow-up questions.",
        "input_schema": schema,
    }
    response = client.messages.create(
        model=resolved_model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_extraction"},
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_extraction":
            return json.dumps(block.input)
    return json.dumps(
        {"known_answers": {}, "field_evidence": {}, "follow_up_questions": []}
    )
