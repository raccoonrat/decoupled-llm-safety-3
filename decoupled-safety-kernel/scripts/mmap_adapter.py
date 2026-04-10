"""
Ring-3 mmap IPC Adapter — Python counterpart of `ipc_mmap_bridge.rs` (Gap 3).

## Shared Frame Layout (mirrors Rust constants exactly)

```
Offset  Size  Field
0x00    4     RING3_READY       (u32, atomic)  Ring-3 → 1 when request ready
0x04    4     KERNEL_DONE       (u32, atomic)  Ring-0 → 1 when result ready
0x08    4     AUDIT_COMMITTED   (u32, atomic)  Ring-0 → 1 after I6 audit
0x0C    4     SEQUENCE          (u32, atomic)  Monotonic step counter
0x10    8     PAYLOAD_LEN       (u64)          Request byte length
0x18    8     RESULT_LEN        (u64)          Result byte length
0x20    32    reserved
0x40    4000  Request region    (JSON bytes)
0xFD8   4000  Result region     (JSON bytes)
```

## Python Atomics Note

Python has the GIL, which provides mutual exclusion within a single process.
For cross-process shared memory (production), the `mmap.flush()` call ensures
writes are visible to the kernel process.  We simulate acquire/release ordering
by calling `mmap.flush()` after every flag write and reading flags before
accessing data regions.

## Production Cross-Process Setup

```python
# Ring-3 side (Python):
shm_path = "/dev/shm/kernel_ipc_trace123"
adapter = MmapRing3Adapter.open_shared(shm_path, create=True, size=FRAME_SIZE)
result = adapter.roundtrip(request)

# Ring-0 side (Rust):
# Rust reads the same /dev/shm path via libc::shm_open + mmap.
```

## Usage (offline / same-process test)

```python
import mmap, json
from scripts.mmap_adapter import MmapRing3Adapter, Ring3Request, Ring0Result

adapter = MmapRing3Adapter.anonymous()
# In a thread/process, simulate Ring-0:
#   adapter.simulate_kernel_serve(handler_fn)
result = adapter.roundtrip(Ring3Request(...))
```
"""

from __future__ import annotations

import json
import mmap
import os
import struct
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Callable, Optional

# ─── Frame layout constants (MUST match ipc_mmap_bridge.rs) ────────────────────

FRAME_SIZE: int = 8_192
PAYLOAD_CAP: int = 4_000

OFF_RING3_READY: int = 0x00     # u32
OFF_KERNEL_DONE: int = 0x04     # u32
OFF_AUDIT_COMMITTED: int = 0x08 # u32
OFF_SEQUENCE: int = 0x0C        # u32
OFF_PAYLOAD_LEN: int = 0x10     # u64
OFF_RESULT_LEN: int = 0x18      # u64
OFF_REQUEST: int = 0x40
OFF_RESULT: int = 0x40 + PAYLOAD_CAP

_U32 = struct.Struct("<I")  # little-endian u32
_U64 = struct.Struct("<Q")  # little-endian u64

# ─── Wire types ────────────────────────────────────────────────────────────────

@dataclass
class ToolCallPayload:
    tool_name: str
    arguments: dict[str, Any]
    asserted_capabilities: list[str] = field(default_factory=list)


@dataclass
class Ring3Request:
    trace_id: str
    step_index: int
    policy_revision: int
    logits: list[float]
    topk_indices: list[int]
    dcbf_margin: float = 0.2
    tool_call: Optional[ToolCallPayload] = None

    def to_json_bytes(self) -> bytes:
        d = asdict(self) if self.tool_call is None else {
            **asdict(self),
            "tool_call": asdict(self.tool_call),
        }
        if self.tool_call is None:
            d.pop("tool_call", None)
        return json.dumps(d).encode()


@dataclass
class ToolVerdict:
    allowed: bool
    reason: str
    forbidden_capabilities_triggered: list[str] = field(default_factory=list)


@dataclass
class Ring0Result:
    trace_id: str
    step_index: int
    policy_revision: int
    feasible: bool
    chosen_index: Optional[int]
    page_fault: bool
    cache_hit: bool
    cache_key_hex: str
    qp_elapsed_us: int
    audit_committed: bool
    tool_verdict: Optional[ToolVerdict] = None

    @staticmethod
    def from_json_bytes(data: bytes) -> "Ring0Result":
        d = json.loads(data)
        tv = d.pop("tool_verdict", None)
        result = Ring0Result(**d)
        if tv is not None:
            result.tool_verdict = ToolVerdict(**tv)
        return result


# ─── Fault types ───────────────────────────────────────────────────────────────

class KernelFault(Exception):
    """Python mirror of Rust KernelFault enum."""

class AuditLockViolation(KernelFault):
    """KERNEL_DONE=1 but AUDIT_COMMITTED=0 — I6 contract violated."""

class FrameCapacityExceeded(KernelFault):
    def __init__(self, required: int):
        super().__init__(f"payload {required} bytes > PAYLOAD_CAP {PAYLOAD_CAP}")

class PrematureRead(KernelFault):
    pass

class StaleSequence(KernelFault):
    def __init__(self, expected: int, found: int):
        super().__init__(f"expected seq={expected}, found seq={found}")

class SpinTimeout(KernelFault):
    pass


# ─── Frame accessor ────────────────────────────────────────────────────────────

class MmapFrameAccessor:
    """Low-level read/write helpers over a Python `mmap.mmap` object."""

    def __init__(self, mm: mmap.mmap) -> None:
        self._mm = mm

    def _read_u32(self, offset: int) -> int:
        return _U32.unpack_from(self._mm, offset)[0]

    def _write_u32(self, offset: int, value: int) -> None:
        _U32.pack_into(self._mm, offset, value)
        self._mm.flush()  # Release semantics approximation for cross-process.

    def _read_u64(self, offset: int) -> int:
        return _U64.unpack_from(self._mm, offset)[0]

    def _write_u64(self, offset: int, value: int) -> None:
        _U64.pack_into(self._mm, offset, value)

    # ── Flag accessors ──────────────────────────────────────────────────────────

    @property
    def ring3_ready(self) -> int: return self._read_u32(OFF_RING3_READY)
    @ring3_ready.setter
    def ring3_ready(self, v: int) -> None: self._write_u32(OFF_RING3_READY, v)

    @property
    def kernel_done(self) -> int: return self._read_u32(OFF_KERNEL_DONE)
    @kernel_done.setter
    def kernel_done(self, v: int) -> None: self._write_u32(OFF_KERNEL_DONE, v)

    @property
    def audit_committed(self) -> int: return self._read_u32(OFF_AUDIT_COMMITTED)
    @audit_committed.setter
    def audit_committed(self, v: int) -> None: self._write_u32(OFF_AUDIT_COMMITTED, v)

    @property
    def sequence(self) -> int: return self._read_u32(OFF_SEQUENCE)
    @sequence.setter
    def sequence(self, v: int) -> None: self._write_u32(OFF_SEQUENCE, v)

    @property
    def payload_len(self) -> int: return self._read_u64(OFF_PAYLOAD_LEN)
    @payload_len.setter
    def payload_len(self, v: int) -> None: self._write_u64(OFF_PAYLOAD_LEN, v)

    @property
    def result_len(self) -> int: return self._read_u64(OFF_RESULT_LEN)
    @result_len.setter
    def result_len(self, v: int) -> None: self._write_u64(OFF_RESULT_LEN, v)

    def write_request_bytes(self, data: bytes) -> None:
        n = len(data)
        self._mm[OFF_REQUEST: OFF_REQUEST + n] = data
        self.payload_len = n

    def read_request_bytes(self) -> bytes:
        n = self.payload_len
        return bytes(self._mm[OFF_REQUEST: OFF_REQUEST + n])

    def write_result_bytes(self, data: bytes) -> None:
        n = len(data)
        self._mm[OFF_RESULT: OFF_RESULT + n] = data
        self.result_len = n

    def read_result_bytes(self) -> bytes:
        n = self.result_len
        return bytes(self._mm[OFF_RESULT: OFF_RESULT + n])


# ─── Ring-3 adapter ────────────────────────────────────────────────────────────

class MmapRing3Adapter:
    """Python Ring-3 side: write request → spin-poll KERNEL_DONE → verify I6 → read result."""

    def __init__(self, mm: mmap.mmap, poll_timeout_s: float = 0.5) -> None:
        self._acc = MmapFrameAccessor(mm)
        self._poll_timeout_s = poll_timeout_s
        self._lock = threading.Lock()  # Serialize multi-threaded Ring-3 callers.

    @classmethod
    def anonymous(cls, poll_timeout_s: float = 0.5) -> "MmapRing3Adapter":
        """Create an in-process anonymous mmap (for testing / speculative decode)."""
        mm = mmap.mmap(-1, FRAME_SIZE)
        return cls(mm, poll_timeout_s)

    @classmethod
    def open_shared(
        cls,
        path: str | Path,
        *,
        create: bool = False,
        poll_timeout_s: float = 0.5,
    ) -> "MmapRing3Adapter":
        """Open / create a file-backed shared mmap (cross-process IPC)."""
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        fd = os.open(str(path), flags, 0o600)
        if create:
            os.ftruncate(fd, FRAME_SIZE)
        mm = mmap.mmap(fd, FRAME_SIZE, mmap.MAP_SHARED)
        os.close(fd)
        return cls(mm, poll_timeout_s)

    def write_request(self, req: Ring3Request) -> int:
        """Write request, increment sequence, signal Ring-0.  Returns sequence number."""
        data = req.to_json_bytes()
        if len(data) > PAYLOAD_CAP:
            raise FrameCapacityExceeded(len(data))

        acc = self._acc
        # Reset flags for this step.
        acc.kernel_done = 0
        acc.audit_committed = 0
        acc.ring3_ready = 0
        # Increment sequence atomically (GIL protects single-process; flush for cross-process).
        seq = (acc.sequence + 1) & 0xFFFF_FFFF
        acc.sequence = seq
        # Write payload then signal.
        acc.write_request_bytes(data)
        acc.ring3_ready = 1  # Flush called inside setter.
        return seq

    def read_result(self, expected_seq: int) -> Ring0Result:
        """Spin-poll KERNEL_DONE, verify I6 lock, decode and return result."""
        acc = self._acc
        # Sequence guard.
        if acc.sequence != expected_seq:
            raise StaleSequence(expected_seq, acc.sequence)

        deadline = time.monotonic() + self._poll_timeout_s
        while acc.kernel_done == 0:
            if time.monotonic() > deadline:
                raise SpinTimeout("timed out waiting for KERNEL_DONE=1")
            time.sleep(1e-6)  # 1 µs spin sleep (avoids Python GIL starvation).

        # I6 verification (Acquire analogue: read after observing KERNEL_DONE=1).
        if acc.audit_committed != 1:
            raise AuditLockViolation(
                "KERNEL_DONE=1 but AUDIT_COMMITTED=0 — I6 contract violated"
            )

        data = acc.read_result_bytes()
        return Ring0Result.from_json_bytes(data)

    def roundtrip(self, req: Ring3Request) -> Ring0Result:
        """Atomic Ring-3 roundtrip: write request → wait → verify I6 → return result."""
        with self._lock:
            seq = self.write_request(req)
            return self.read_result(seq)

    def simulate_kernel_serve(
        self,
        handler: Callable[[Ring3Request], tuple["Ring0Result", bool]],
    ) -> None:
        """
        Inline kernel simulation for testing (no Rust binary required).

        `handler` receives the deserialized request and returns
        `(Ring0Result, audit_committed_bool)`.
        """
        acc = self._acc
        deadline = time.monotonic() + self._poll_timeout_s
        while acc.ring3_ready == 0:
            if time.monotonic() > deadline:
                raise SpinTimeout("kernel sim: timed out waiting for RING3_READY=1")
            time.sleep(1e-6)

        # Consume the request: reset ring3_ready to 0 immediately after reading.
        # This prevents the next simulation thread from seeing a stale ring3_ready=1
        # from the previous step (mirrors the Ring-0 Rust side's SPSC discipline).
        acc.ring3_ready = 0
        req_bytes = acc.read_request_bytes()
        d = json.loads(req_bytes)
        req = Ring3Request(
            trace_id=d["trace_id"],
            step_index=d["step_index"],
            policy_revision=d["policy_revision"],
            logits=d["logits"],
            topk_indices=d["topk_indices"],
            dcbf_margin=d.get("dcbf_margin", 0.2),
            tool_call=(
                ToolCallPayload(**d["tool_call"])
                if d.get("tool_call") else None
            ),
        )
        result, audit_ok = handler(req)

        result_bytes = json.dumps(asdict(result) if result.tool_verdict is None else {
            **{k: v for k, v in asdict(result).items() if k != "tool_verdict"},
            "tool_verdict": asdict(result.tool_verdict) if result.tool_verdict else None,
        }).encode()
        if len(result_bytes) > PAYLOAD_CAP:
            raise FrameCapacityExceeded(len(result_bytes))

        acc.write_result_bytes(result_bytes)
        # I6 LOCK: AUDIT_COMMITTED before KERNEL_DONE.
        acc.audit_committed = 1 if audit_ok else 0
        acc.kernel_done = 1


# ─── Offline self-test ─────────────────────────────────────────────────────────

def _selftest() -> None:
    """Verify the Python IPC adapter protocol works end-to-end."""
    print("mmap_adapter: running offline self-test …", flush=True)
    adapter = MmapRing3Adapter.anonymous(poll_timeout_s=2.0)

    req = Ring3Request(
        trace_id="selftest-001",
        step_index=0,
        policy_revision=1,
        logits=[1.0, 2.0, 3.0],
        topk_indices=[0, 1, 2],
        dcbf_margin=0.5,
    )

    def kernel_handler(r: Ring3Request) -> tuple[Ring0Result, bool]:
        assert r.trace_id == "selftest-001"
        return Ring0Result(
            trace_id=r.trace_id,
            step_index=r.step_index,
            policy_revision=r.policy_revision,
            feasible=True,
            chosen_index=2,
            page_fault=False,
            cache_hit=False,
            cache_key_hex="0xdeadbeef",
            qp_elapsed_us=8,
            audit_committed=True,
        ), True

    # Run kernel simulation in a separate thread (mirrors cross-process model).
    t = threading.Thread(target=adapter.simulate_kernel_serve, args=(kernel_handler,))
    t.start()
    result = adapter.roundtrip(req)
    t.join()

    assert result.feasible, "self-test: expected feasible=True"
    assert result.chosen_index == 2, f"self-test: expected chosen_index=2, got {result.chosen_index}"
    assert result.audit_committed, "self-test: expected audit_committed=True"
    print(f"mmap_adapter: PASS — chosen={result.chosen_index}, audit={result.audit_committed}")

    # I6 violation detection test.
    req2 = Ring3Request(
        trace_id="selftest-i6", step_index=1, policy_revision=1,
        logits=[1.0], topk_indices=[0], dcbf_margin=0.3,
    )
    def bad_kernel(r: Ring3Request) -> tuple[Ring0Result, bool]:
        res = Ring0Result(
            trace_id=r.trace_id, step_index=r.step_index, policy_revision=r.policy_revision,
            feasible=True, chosen_index=0, page_fault=False, cache_hit=False,
            cache_key_hex="0x0", qp_elapsed_us=0, audit_committed=False,
        )
        return res, False  # ← I6 violation: audit_committed=False

    t2 = threading.Thread(target=adapter.simulate_kernel_serve, args=(bad_kernel,))
    t2.start()
    try:
        adapter.roundtrip(req2)
        raise AssertionError("self-test: expected AuditLockViolation")
    except AuditLockViolation:
        print("mmap_adapter: PASS — I6 audit lock violation correctly detected")
    t2.join()


if __name__ == "__main__":
    _selftest()
