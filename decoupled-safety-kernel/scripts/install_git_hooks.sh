#!/usr/bin/env bash
# Point this repository at .githooks so pre-commit runs verify_ssot_compliance.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${ROOT}"
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit 2>/dev/null || true
echo "git core.hooksPath set to .githooks (pre-commit -> verify_ssot_compliance.py)"
