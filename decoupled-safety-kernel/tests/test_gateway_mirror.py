"""Gateway mirror parity with Rust (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from llm_user_space.gateway_mirror import GatewayHardReject, sanitize_input


class TestGatewayMirror(unittest.TestCase):
    def test_critical_rejects(self) -> None:
        with self.assertRaises(GatewayHardReject):
            sanitize_input(b"x __CRITICAL_TEST__ y")

    def test_safe_passes(self) -> None:
        sp = sanitize_input(b"normal")
        self.assertTrue(len(sp.canonical) > 0)


if __name__ == "__main__":
    unittest.main()
