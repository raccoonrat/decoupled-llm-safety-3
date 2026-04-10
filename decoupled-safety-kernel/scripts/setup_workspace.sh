#!/usr/bin/env bash
# Idempotent scaffold for decoupled-safety-kernel top-level layout (SSOT-aligned).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

mkdir -p \
  "${KERNEL_ROOT}/llm_user_space" \
  "${KERNEL_ROOT}/src_kernel/src/gateway" \
  "${KERNEL_ROOT}/src_observability/probe_ensemble" \
  "${KERNEL_ROOT}/src_eval_benchmark" \
  "${KERNEL_ROOT}/rfc_contracts/v1" \
  "${KERNEL_ROOT}/docs/theory" \
  "${KERNEL_ROOT}/docs/rfc" \
  "${KERNEL_ROOT}/scripts"

echo "decoupled-safety-kernel workspace directories ensured under: ${KERNEL_ROOT}"
