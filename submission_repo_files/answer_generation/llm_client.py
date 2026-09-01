"""
Thin Anthropic Messages API client using raw HTTPS (httpx), not the
`anthropic` Python SDK.

Why: the DNA doc (§7) specifies a portable app that calls the Anthropic API
directly rather than routing through a chat subscription. The obvious way to
do that is the official `anthropic` SDK — but this cloud sandbox blocks
pip-installing anything not already cached, and `anthropic` isn't cached
here. `httpx` is already available, and `api.anthropic.com` is reachable
from this sandbox (confirmed with a live test call), so this module talks to
the Messages API directly over HTTPS instead.

This is a documented substitution, same pattern as embeddings/embedder.py
and vectorstore/local_store.py: the public function here (`call_model`) is
the seam. On a machine with normal pip access, swapping this module's
internals for the real SDK is a contained change — nothing else in
answer_generation/ needs to know which one is being used.
"""
import logging

import httpx

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_API_BASE,
    ANTHROPIC_API_VERSION,
    MODEL_DEFAULT,
)

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class LLMError(RuntimeError):
    """Raised when the Anthropic API call fails or returns something the
    caller can't use (missing key, HTTP error, malformed tool-use response)."""


def call_model(
    system: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    model: str = MODEL_DEFAULT,
    max_tokens: int = 2000,
    cache_system: bool = True,
) -> dict:
    """Call the Messages API and return the parsed JSON body. Raises
    LLMError on any failure rather than letting callers deal with raw httpx
    exceptions or malformed responses.

    cache_system=True marks the system prompt for prompt caching (DNA doc
    §7 cost engineering: cache hits cost 10% of base input price). Since the
    system prompt carries the retrieved document context, which is the same
    within a single question but changes per-question, caching only pays off
    for retries/multi-turn — it's harmless to leave on by default.
    """
    if not ANTHROPIC_API_KEY:
        raise LLMError(
            "ANTHROPIC_API_KEY is not set. Add it to .env before calling the "
            "answer-generation layer."
        )

    system_blocks = [{
        "type": "text",
        "text": system,
        **({"cache_control": {"type": "ephemeral"}} if cache_system else {}),
    }]

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    try:
        resp = httpx.post(
            ANTHROPIC_API_BASE,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            json=payload,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise LLMError(f"Network error calling Anthropic API: {e}") from e

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {}
        raise LLMError(
            f"Anthropic API returned HTTP {resp.status_code}: "
            f"{err.get('type', 'unknown')} — {err.get('message', resp.text[:300])}"
        )

    body = resp.json()
    logger.info(
        "LLM call ok: model=%s in=%d out=%d cache_read=%d",
        model,
        body.get("usage", {}).get("input_tokens", 0),
        body.get("usage", {}).get("output_tokens", 0),
        body.get("usage", {}).get("cache_read_input_tokens", 0),
    )
    return body


def extract_tool_input(response_body: dict, tool_name: str) -> dict:
    """Pull the JSON input out of a forced tool-use response. Raises
    LLMError if the model didn't call the expected tool (shouldn't happen
    with tool_choice forcing it, but real APIs can surprise you)."""
    for block in response_body.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            return block.get("input", {})
    raise LLMError(
        f"Expected a '{tool_name}' tool call in the model response but didn't "
        f"get one. Response content types: "
        f"{[b.get('type') for b in response_body.get('content', [])]}"
    )
