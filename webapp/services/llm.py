from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from webapp.config import LLM_MODEL, get_llm_client


def get_client() -> OpenAI:
    return get_llm_client()


def extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty LLM response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.I)
    if fence:
        return json.loads(fence.group(1))
    start = text.find("{")
    if start < 0:
        start = text.find("[")
    if start >= 0:
        return json.loads(text[start:])
    raise ValueError(f"Could not parse JSON from: {text[:200]}")


def chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    model: str | None = None,
    caller: str = "chat",
) -> str:
    from webapp.llm.request_log import logged_chat_completions_create

    client = get_client()
    resp = logged_chat_completions_create(
        client,
        caller=caller,
        model=model or LLM_MODEL,
        messages=messages,
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()
