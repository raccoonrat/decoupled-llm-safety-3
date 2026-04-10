"""Tests for Ring-3 env parsing (no network)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_ROOT))

from llm_user_space.env_loader import load_env_file, parse_env_line


class TestEnvLoader(unittest.TestCase):
    def test_parse_export_quoted(self) -> None:
        p = parse_env_line("export FOO='bar'")
        self.assertIsNotNone(p)
        self.assertEqual(p, ("FOO", "bar"))

    def test_load_file_sets_env(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False, encoding="utf-8"
        ) as f:
            f.write("export ZZ_TEST_KEY='hello'\n")
            path = f.name
        try:
            os.environ.pop("ZZ_TEST_KEY", None)
            n = load_env_file(path)
            self.assertEqual(n, 1)
            self.assertEqual(os.environ.get("ZZ_TEST_KEY"), "hello")
        finally:
            os.environ.pop("ZZ_TEST_KEY", None)
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
