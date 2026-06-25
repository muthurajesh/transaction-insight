from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

_LOG = logging.getLogger("transaction_insight.llm")
_CONFIGURED = False


def _env_bool(name: str, *, default: bool = True) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on")


def llm_logging_enabled() -> bool:
    return _env_bool("LLM_LOG_CALLS", default=True)


def setup_llm_logging() -> None:
    """Attach console + file handlers for LLM call tracing (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED or not llm_logging_enabled():
        return

    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(isinstance(h, logging.StreamHandler) for h in _LOG.handlers):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        _LOG.addHandler(console)

    log_file = os.getenv("LLM_LOG_FILE", "data/llm.log").strip()
    if log_file and not any(
        isinstance(h, logging.FileHandler) for h in _LOG.handlers
    ):
        path = Path(log_file)
        if not path.is_absolute():
            root = Path(__file__).resolve().parents[2]
            path = root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        _LOG.addHandler(file_handler)
        _LOG.info("LLM logging enabled — writing to %s", path)

    _CONFIGURED = True


def _body_limit() -> int:
    return max(500, int(os.getenv("LLM_LOG_BODY_MAX_CHARS", "8000")))


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").replace("\r\n", "\n")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}… [{len(text)} chars total]"


def _format_messages(messages: list[dict[str, Any]], max_chars: int) -> str:
    per_msg = max(200, max_chars // max(len(messages), 1))
    lines: list[str] = []
    for index, message in enumerate(messages):
        role = message.get("role", "?")
        content = message.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"  [{index}] {role}: {_truncate(content, per_msg)}")
    return "\n".join(lines)


def logged_chat_completions_create(
    client: OpenAI,
    *,
    caller: str,
    **kwargs: Any,
):
    """Wrap OpenAI chat.completions.create with request/response logging and timing."""
    if not llm_logging_enabled():
        return client.chat.completions.create(**kwargs)

    setup_llm_logging()

    model = kwargs.get("model", "?")
    messages: list[dict[str, Any]] = list(kwargs.get("messages") or [])
    temperature = kwargs.get("temperature")
    extras: list[str] = []
    if temperature is not None:
        extras.append(f"temp={temperature}")
    if kwargs.get("response_format"):
        extras.append(f"json_mode={kwargs['response_format']}")
    extra_text = f" ({', '.join(extras)})" if extras else ""

    _LOG.info(
        "LLM START caller=%s model=%s messages=%d%s\n%s",
        caller,
        model,
        len(messages),
        extra_text,
        _format_messages(messages, _body_limit()),
    )

    started = time.perf_counter()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        _LOG.error(
            "LLM FAILED caller=%s model=%s elapsed=%.3fs error=%s",
            caller,
            model,
            elapsed,
            exc,
        )
        raise

    elapsed = time.perf_counter() - started
    content = ""
    try:
        content = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        pass

    _LOG.info(
        "LLM DONE caller=%s model=%s elapsed=%.3fs response_chars=%d\n  response: %s",
        caller,
        model,
        elapsed,
        len(content),
        _truncate(content, _body_limit()),
    )
    return response
