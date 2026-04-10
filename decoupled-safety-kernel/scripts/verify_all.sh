#!/usr/bin/env bash
# One-shot: Rust build + tests + clippy, SSOT governance, Python unittest (conda env `decoupled` recommended).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO="$(cd "${KERNEL}/.." && pwd)"

echo "==> decoupled-safety-kernel: cargo build --release"
(cd "${KERNEL}" && cargo build --release)

echo "==> decoupled-safety-kernel: cargo test (unit + e2e_v01 integration)"
(cd "${KERNEL}" && cargo test)

echo "==> decoupled-safety-kernel: cargo clippy (-D warnings)"
(cd "${KERNEL}" && cargo clippy --all-targets -- -D warnings)

echo "==> SSOT: verify_ssot_compliance.py"
python3 "${KERNEL}/scripts/verify_ssot_compliance.py" --root "${REPO}"

echo "==> Python: compileall"
python3 -m compileall -q "${KERNEL}/src_observability" "${KERNEL}/src_eval_benchmark" "${KERNEL}/llm_user_space"

echo "==> Python: unittest (DCBF / observability)"
PYTHONPATH="${KERNEL}" python3 -m unittest discover -s "${KERNEL}/tests" -p 'test_*.py' -v

echo "==> Optional: full-chain E2E (DeepSeek + Ring-0 binary) — requires env + network:"
echo "    cargo build --bin e2e_ring3_json && PYTHONPATH=${KERNEL} python3 ${KERNEL}/scripts/e2e_full_chain.py"
echo "    (autoregressive: default; one-step stdout = Ring-0 JSON only: add --single-step)"

echo "verify_all: OK"
