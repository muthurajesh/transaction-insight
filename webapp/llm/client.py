from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from webapp.processing.constants import CONFIG_DIR

load_dotenv(CONFIG_DIR / ".env")


BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))

LOCAL_BATCH_SIZE = int(os.getenv("LOCAL_BATCH_SIZE", "40"))

DESCRIPTION_BATCH_SIZE = int(os.getenv("DESCRIPTION_BATCH_SIZE", "0"))

CLASSIFICATION_BATCH_SIZE = int(os.getenv("CLASSIFICATION_BATCH_SIZE", "0"))

LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://192.168.0.7:1234/v1")

LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "qwen2.5-coder-32b-instruct")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "600"))

PIPELINE_LLM_TEMPERATURE = float(os.getenv("PIPELINE_LLM_TEMPERATURE", "0"))

LOCAL_PROVIDERS = frozenset({"lmstudio", "ollama"})

def json_for_prompt(obj: Any) -> str:
    """Compact JSON for LLM prompts (smaller/faster than pretty-printed)."""
    return json.dumps(obj, separators=(",", ":"))

def resolve_batch_sizes(
    provider: str,
    *,
    batch_size_arg: int | None,
) -> tuple[int, int, int]:
    """
    Return (general, description, classification) batch sizes.
    Local providers default to LOCAL_BATCH_SIZE unless --batch-size is set.
    """
    if batch_size_arg is not None:
        base = batch_size_arg
    elif provider in LOCAL_PROVIDERS:
        base = LOCAL_BATCH_SIZE
    else:
        base = BATCH_SIZE

    desc = DESCRIPTION_BATCH_SIZE if DESCRIPTION_BATCH_SIZE > 0 else base
    if CLASSIFICATION_BATCH_SIZE > 0:
        classify = CLASSIFICATION_BATCH_SIZE
    else:
        # Classification returns more tokens per row; cap slightly on local runs.
        classify = min(base, 25) if provider in LOCAL_PROVIDERS else base
    return base, desc, classify

def _role_model_env(role: str | None) -> str | None:
    """PIPELINE_MODEL or CHAT_MODEL when LM Studio loads multiple models."""
    if not role:
        return None
    key = f"{role.strip().lower()}_model"
    return os.getenv(key.upper(), "").strip() or None

def resolve_provider_config(
    provider_arg: str,
    *,
    base_url_arg: str | None,
    model_arg: str | None,
    role: str | None = None,
) -> tuple[str, str, str]:
    """
    Return (provider, base_url, model) from CLI args and environment.

    Precedence (highest first):
      1. Explicit CLI/API args (base_url_arg, model_arg)
      2. Role env (PIPELINE_MODEL or CHAT_MODEL when role is set)
      3. Generic env overrides (LLM_BASE_URL, LLM_MODEL)
      4. Provider-specific env (LM_STUDIO_*, OLLAMA_*, OPENAI_*)
         chosen by LLM_PROVIDER
    """
    env_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    provider = provider_arg
    if provider == "auto":
        if env_provider in LOCAL_PROVIDERS | {"openai"}:
            provider = env_provider
        else:
            provider = "lmstudio"

    env_base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    env_model = os.getenv("LLM_MODEL", "").strip() or None
    role_model = _role_model_env(role)
    base_override = base_url_arg or env_base_url
    model_override = model_arg or role_model or env_model

    if provider == "ollama":
        base_url = (base_override or OLLAMA_BASE_URL).rstrip("/")
        model = model_override or OLLAMA_MODEL
    elif provider == "lmstudio":
        base_url = (base_override or LM_STUDIO_BASE_URL).rstrip("/")
        model = model_override or LM_STUDIO_MODEL
    elif provider == "openai":
        base_url = ""
        model = model_override or OPENAI_MODEL
    else:
        raise ValueError(f"Unknown provider: {provider}")

    return provider, base_url, model

def create_client(provider: str, *, base_url: str) -> OpenAI:
    """Return an OpenAI-compatible client for the chosen provider."""
    if provider in LOCAL_PROVIDERS:
        api_key_env = "OLLAMA_API_KEY" if provider == "ollama" else "LM_STUDIO_API_KEY"
        default_key = "ollama" if provider == "ollama" else "lm-studio"
        return OpenAI(
            base_url=base_url.rstrip("/"),
            api_key=os.getenv(api_key_env, default_key),
            timeout=REQUEST_TIMEOUT,
        )
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Use --provider ollama or lmstudio for a local "
            "OpenAI-compatible server, or set OPENAI_API_KEY for OpenAI cloud."
        )
    return OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT)
