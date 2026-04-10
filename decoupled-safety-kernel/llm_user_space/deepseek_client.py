"""
Ring-3 DeepSeek adapter — OpenAI-compatible HTTP API (no safety logic).

API reference: https://api-docs.deepseek.com/zh-cn/
Base URL: https://api.deepseek.com (or https://api.deepseek.com/v1 for OpenAI SDK compatibility).

Requires: `pip install openai`
Environment: `DEEPSEEK_API_KEY`; optional `GATEWAY_UPSTREAM` as base_url override.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .env_loader import load_default_repo_env

# Lazy import so importing this module without `openai` still allows env loading.
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[misc, assignment]

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


@dataclass
class DeepSeekConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL


def _ensure_openai() -> None:
    if OpenAI is None:
        raise ImportError(
            "openai package required: pip install openai"
        )


def build_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    load_env: bool = True,
) -> Any:
    """
    Construct an OpenAI-compatible client pointed at DeepSeek.
    Call with `load_env=True` (default) to merge `<repo_root>/env` into the process.
    """
    if load_env:
        load_default_repo_env()
    _ensure_openai()
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError(
            "DEEPSEEK_API_KEY missing: set in environment or repo `env` file"
        )
    base = (base_url or os.environ.get("GATEWAY_UPSTREAM") or DEFAULT_BASE_URL).rstrip(
        "/"
    )
    return OpenAI(api_key=key, base_url=base)


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    stream: bool = False,
    load_env: bool = True,
    **kwargs: Any,
) -> Any:
    """Single-turn or multi-turn chat; returns SDK response object."""
    client = build_client(load_env=load_env)
    return client.chat.completions.create(
        model=model,
        messages=messages,
        stream=stream,
        **kwargs,
    )


def complete_user_prompt(
    user_text: str,
    *,
    system: str = "You are a helpful assistant.",
    model: str = DEFAULT_MODEL,
) -> str:
    """Convenience: return assistant message content as str (non-streaming)."""
    resp = chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        model=model,
        stream=False,
    )
    return (resp.choices[0].message.content or "").strip()


if __name__ == "__main__":
    # Manual smoke: `PYTHONPATH=decoupled-safety-kernel python -m llm_user_space.deepseek_client`
    print(complete_user_prompt("Reply with exactly one word: OK"))
