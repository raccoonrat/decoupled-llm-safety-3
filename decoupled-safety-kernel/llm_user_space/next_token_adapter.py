"""
Ring-3: next-token top logprobs → dense `logits` + `topk_indices` for Ring-0 `ProjectionInput`.

Uses OpenAI-compatible `logprobs` / `top_logprobs` on the first generated token (DeepSeek API).
See: https://api-docs.deepseek.com/zh-cn/

If the API omits logprobs, falls back to a single-token completion without scores (caller may abort).
"""

from __future__ import annotations

from dataclasses import dataclass

from .deepseek_client import DEFAULT_MODEL, build_client
from .env_loader import load_default_repo_env


@dataclass
class NextTokenTopK:
    """Aligned with Rust `ProjectionInput`: `logits[i]` used for vocab index `i`."""

    logits: list[float]
    topk_indices: list[int]
    raw_top_logprobs: list[tuple[str, float]]


def _extract_top_logprobs(response: object) -> list[tuple[str, float]]:
    """Parse OpenAI-compatible chat completion logprobs for the first token."""
    try:
        tops = response.choices[0].logprobs.content[0].top_logprobs  # type: ignore[union-attr]
    except (AttributeError, IndexError, TypeError):
        return []
    out: list[tuple[str, float]] = []
    for item in tops or []:
        tok = getattr(item, "token", None)
        tlp = getattr(item, "logprob", None)
        if isinstance(item, dict):
            tok = tok or item.get("token")
            tlp = tlp if tlp is not None else item.get("logprob")
        if tok is not None and tlp is not None:
            out.append((str(tok), float(tlp)))
    return out


def fetch_next_token_topk(
    messages: list[dict[str, str]],
    *,
    top_k: int = 5,
    model: str = DEFAULT_MODEL,
    load_env: bool = True,
) -> NextTokenTopK:
    """
    Request exactly one new token with top logprobs; map to sequential indices `0..K-1`
    so `logits.get(i)` matches Rust projection without a full vocab table.
    """
    if load_env:
        load_default_repo_env()
    client = build_client(load_env=False)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=1,
        logprobs=True,
        top_logprobs=min(top_k, 20),
        stream=False,
    )
    tops = _extract_top_logprobs(response)
    if not tops:
        # Fallback: no logprobs — use message content as single pseudo-candidate
        text = (response.choices[0].message.content or "").strip() or "?"
        tops = [(text, 0.0)]

    tops = tops[:top_k]
    k = len(tops)
    logits = [0.0] * k
    topk_indices = list(range(k))
    for i, (_tok, logprob) in enumerate(tops):
        logits[i] = logprob
    return NextTokenTopK(
        logits=logits,
        topk_indices=topk_indices,
        raw_top_logprobs=tops,
    )
