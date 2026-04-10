"""
HTTP client for the Track A judge service.

The judge service is an evaluation-layer component (Ring-4).
It MUST NOT enter Ring-0.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def http_judge(
    prompt: str,
    completion: str,
    *,
    url: str = "http://localhost:8199/judge",
    timeout_s: float = 10.0,
) -> tuple[bool | None, bool | None]:
    """
    Call the HTTP judge service.

    Returns (refusal_detected, harmful_detected).
    Falls back to (None, None) on communication error.
    """
    payload = json.dumps({
        "prompt": prompt,
        "completion": completion,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return bool(body.get("refusal_detected")), bool(body.get("harmful_detected"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None, None
