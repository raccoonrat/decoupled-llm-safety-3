"""
Load shell-style `env` files (`export KEY=value`) into `os.environ`.

Default search: repository root `env` (same file teams `source` in shell).
Does not override keys already present in the process environment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_EXPORT_RE = re.compile(r"^\s*export\s+(.+)$")


def _strip_quotes(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def parse_env_line(line: str) -> tuple[str, str] | None:
    t = line.strip()
    if not t or t.startswith("#"):
        return None
    m = _EXPORT_RE.match(t)
    if m:
        t = m.group(1).strip()
    if "=" not in t:
        return None
    key, _, value = t.partition("=")
    key = key.strip()
    if not key:
        return None
    return key, _strip_quotes(value)


def load_env_file(path: Path | str, *, override: bool = False) -> int:
    """
    Returns number of variables set.
    If `override` is False, existing `os.environ` entries are kept.
    """
    p = Path(path)
    if not p.is_file():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(line)
        if parsed is None:
            continue
        k, v = parsed
        if not override and k in os.environ:
            continue
        os.environ[k] = v
        n += 1
    return n


def load_default_repo_env() -> int:
    """
    Load `<repo_root>/env` where `repo_root` is three levels above this file:
    `decoupled-safety-kernel/llm_user_space/env_loader.py` -> repo root.
    """
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    return load_env_file(repo_root / "env")
