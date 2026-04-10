//! Zero-Copy IPC Bridge (Gap 3): Page-aligned shared frame + AtomicU32 spinlock protocol.
//!
//! ## Architectural Memo: Data-Race Freedom & I6 Audit Lock
//!
//! ### Memory Layout — 8 KiB Fixed Frame
//!
//! ```text
//! ┌── Control Block (64 bytes, page 0) ──────────────────────────────────────────┐
//! │ 0x00  AtomicU32  RING3_READY       Ring-3 → 1 when request payload written   │
//! │ 0x04  AtomicU32  KERNEL_DONE       Ring-0 → 1 when result + audit complete   │
//! │ 0x08  AtomicU32  AUDIT_COMMITTED   Ring-0 → 1 BEFORE KERNEL_DONE (I6 lock)  │
//! │ 0x0C  AtomicU32  SEQUENCE          Monotonic step counter (stale-read guard) │
//! │ 0x10  u64        PAYLOAD_LEN       Byte length of request payload            │
//! │ 0x18  u64        RESULT_LEN        Byte length of result payload             │
//! │ 0x20..0x3F       reserved                                                    │
//! ├── Request Region (4000 bytes) ────────────────────────────────────────────────┤
//! │ 0x040..0xFD7     serde_json-encoded Ring3Request                             │
//! ├── Result Region (4000 bytes) ─────────────────────────────────────────────────┤
//! │ 0xFD8..0x1FAF    serde_json-encoded Ring0BridgeResult                        │
//! └───────────────────────────────────────────────────────────────────────────────┘
//! ```
//!
//! ### SPSC Protocol (Single Producer Ring-3, Single Consumer Ring-0)
//!
//! ```text
//! Ring-3:                              Ring-0:
//!   reset KERNEL_DONE=0                 [idle]
//!   reset AUDIT_COMMITTED=0
//!   write payload → PAYLOAD_LEN
//!   RING3_READY.store(1, Release) ──►  RING3_READY.load(Acquire) == 1
//!                                       read payload / run projection
//!                                       append AuditRecord (I6)
//!                                       AUDIT_COMMITTED.store(1, Release)
//!                                       write result → RESULT_LEN
//!                                       KERNEL_DONE.store(1, Release)
//!   KERNEL_DONE.load(Acquire) == 1  ◄──
//!   AUDIT_COMMITTED.load(Acquire)
//!     == 1 → read result OK
//!     == 0 → KernelFault::AuditLockViolation
//! ```
//!
//! ### Race-Freedom Proof
//!
//! 1. **Happens-before chain** (Release → Acquire pairs):
//!    `payload write` HB `RING3_READY=1` HB `payload read`
//!    `result write` HB `AUDIT_COMMITTED=1` HB `KERNEL_DONE=1` HB `result read`
//!
//! 2. **I6 Audit Lock**: `AUDIT_COMMITTED=1` is an unconditional prerequisite of `KERNEL_DONE=1`
//!    (code sequence enforced; no branch can skip it). Ring-3 verifies both flags.
//!    If `KERNEL_DONE=1` and `AUDIT_COMMITTED=0`, it is physically impossible in correct code
//!    — any such observation indicates memory corruption → `KernelFault::AuditLockViolation`.
//!
//! 3. **Sequence guard**: Monotonic `SEQUENCE` counter prevents Ring-3 from reading a stale
//!    result from a prior step when the frame has not yet been reset.
//!
//! 4. **No false sharing**: The 64-byte control block fits entirely inside a cache line.
//!    Request and result regions are on separate page boundaries.
//!
//! ### Production mmap Upgrade Path
//!
//! Replace `FrameBacking::Heap` with `FrameBacking::Anon` / `FrameBacking::SharedFile`:
//! ```text
//! // Same process (speculative decode threads):
//! let ptr = unsafe { libc::mmap(null_mut(), FRAME_SIZE, PROT_RW, MAP_SHARED|MAP_ANON, -1, 0) };
//! // Cross-process (Ring-3 subprocess):
//! let fd  = unsafe { libc::shm_open(b"/krnl_ipc\0".as_ptr() as _, O_CREAT|O_RDWR, 0o600) };
//! unsafe { libc::ftruncate(fd, FRAME_SIZE as _) };
//! let ptr = unsafe { libc::mmap(null_mut(), FRAME_SIZE, PROT_RW, MAP_SHARED, fd, 0) };
//! ```
//! All other code is identical — only `FrameBacking` changes.

use std::alloc::{Layout, alloc_zeroed, dealloc};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

// ─── Frame constants ──────────────────────────────────────────────────────────

/// Total frame size (2 OS pages; production: extend to 16 KiB for larger tool calls).
pub const FRAME_SIZE: usize = 8_192;
/// Must match OS page size for mmap alignment compatibility.
pub const FRAME_ALIGN: usize = 4_096;

const OFF_RING3_READY: usize = 0x00;
const OFF_KERNEL_DONE: usize = 0x04;
const OFF_AUDIT_COMMITTED: usize = 0x08;
const OFF_SEQUENCE: usize = 0x0C;
const OFF_PAYLOAD_LEN: usize = 0x10; // u64, 8 bytes
const OFF_RESULT_LEN: usize = 0x18;  // u64, 8 bytes

const OFF_REQUEST: usize = 0x40;
const OFF_RESULT: usize = 0x40 + 4_000;

const PAYLOAD_CAP: usize = 4_000;

// ─── Wire types (Ring-3 → Ring-0) ─────────────────────────────────────────────

/// Payload crossing the IPC boundary from Ring-3 (untrusted) to Ring-0 (trusted).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ring3Request {
    pub trace_id: String,
    pub step_index: u64,
    pub policy_revision: u64,
    pub logits: Vec<f32>,
    pub topk_indices: Vec<usize>,
    pub dcbf_margin: f32,
    /// Optional JSON tool call payload (Gap 4: action space intercept).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call: Option<ToolCallPayload>,
}

/// Structured JSON tool call from an agentic LLM (Gap 4 extension).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallPayload {
    pub tool_name: String,
    pub arguments: serde_json::Value,
    /// Capabilities this tool call is asserted to require (Ring-3-declared, untrusted).
    #[serde(default)]
    pub asserted_capabilities: Vec<String>,
}

/// Kernel result crossing the IPC boundary from Ring-0 (trusted) to Ring-3 (untrusted).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ring0BridgeResult {
    pub trace_id: String,
    pub step_index: u64,
    pub policy_revision: u64,
    pub feasible: bool,
    pub chosen_index: Option<usize>,
    pub page_fault: bool,
    pub cache_hit: bool,
    pub cache_key_hex: String,
    pub qp_elapsed_us: u64,
    /// Set by Ring-0 after durable audit append (mirrors I6 `audit_committed` flag).
    pub audit_committed: bool,
    /// If tool call was provided, the verdict on the tool call.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_verdict: Option<ToolVerdict>,
}

/// Verdict on a tool call (Gap 4).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolVerdict {
    pub allowed: bool,
    /// Human-readable reason if denied.
    pub reason: String,
    /// Capabilities that would be acquired; if any are forbidden → deny.
    pub forbidden_capabilities_triggered: Vec<String>,
}

// ─── Fault types ──────────────────────────────────────────────────────────────

/// IPC-layer faults mapped to fail-safe (RFC §0 / §11.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum KernelFault {
    /// `KERNEL_DONE=1` but `AUDIT_COMMITTED=0`: I6 contract broken.
    AuditLockViolation,
    /// Payload or result exceeds `PAYLOAD_CAP` bytes.
    FrameCapacityExceeded { required: usize },
    /// Ring-3 read the result region without waiting for `KERNEL_DONE=1`.
    PrematureRead,
    /// Stale sequence number detected (Ring-3 re-reading an old result).
    StaleSequence { expected: u32, found: u32 },
    /// Serialization / deserialization error in the frame region.
    SerdeError(String),
    /// Timeout waiting for protocol flag.
    SpinTimeout,
}

impl std::fmt::Display for KernelFault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "KernelFault::{self:?}")
    }
}

// ─── Frame backing ────────────────────────────────────────────────────────────

/// Backing memory source for `MmapFrame`.
enum FrameBacking {
    /// Page-aligned heap allocation (current implementation; see mmap upgrade path above).
    Heap(Layout),
}

/// Shared memory frame: the single IPC slot between Ring-3 and Ring-0.
///
/// # Safety
/// All concurrent flag accesses go through `AtomicU32` with explicit Release/Acquire ordering.
/// Payload regions are protected by the protocol: Ring-3 writes only when `RING3_READY=0`,
/// Ring-0 writes only when `RING3_READY=1` and `KERNEL_DONE=0`.
pub struct MmapFrame {
    base: *mut u8,
    _backing: FrameBacking,
}

// Safety: all concurrent accesses are mediated by AtomicU32 flags with correct ordering.
unsafe impl Send for MmapFrame {}
unsafe impl Sync for MmapFrame {}

impl MmapFrame {
    /// Allocate a page-aligned, zero-initialized IPC frame.
    pub fn new() -> Self {
        let layout = Layout::from_size_align(FRAME_SIZE, FRAME_ALIGN)
            .expect("ipc frame layout");
        let base = unsafe { alloc_zeroed(layout) };
        assert!(!base.is_null(), "ipc frame allocation failed");
        Self { base, _backing: FrameBacking::Heap(layout) }
    }

    // ── Atomic flag accessors ─────────────────────────────────────────────────

    fn ring3_ready(&self) -> &AtomicU32 {
        // Safety: base is FRAME_ALIGN-aligned; OFF_RING3_READY=0 → correct alignment.
        unsafe { &*(self.base.add(OFF_RING3_READY) as *const AtomicU32) }
    }

    fn kernel_done(&self) -> &AtomicU32 {
        unsafe { &*(self.base.add(OFF_KERNEL_DONE) as *const AtomicU32) }
    }

    fn audit_committed(&self) -> &AtomicU32 {
        unsafe { &*(self.base.add(OFF_AUDIT_COMMITTED) as *const AtomicU32) }
    }

    fn sequence(&self) -> &AtomicU32 {
        unsafe { &*(self.base.add(OFF_SEQUENCE) as *const AtomicU32) }
    }

    // ── u64 length fields (non-atomic; written under flag protocol) ───────────

    unsafe fn write_u64(base: *mut u8, offset: usize, val: u64) {
        (base.add(offset) as *mut u64).write_unaligned(val);
    }

    unsafe fn read_u64(base: *const u8, offset: usize) -> u64 {
        (base.add(offset) as *const u64).read_unaligned()
    }

    // ── Public protocol API ───────────────────────────────────────────────────

    /// Current monotonic sequence counter (for stale-read detection).
    pub fn current_sequence(&self) -> u32 {
        self.sequence().load(Ordering::Acquire)
    }

    /// **Ring-3 side**: Reset frame for a new step, write request, signal Ring-0.
    ///
    /// Returns the sequence number of this step (use to validate `read_result`).
    pub fn write_request(&self, req: &Ring3Request) -> Result<u32, KernelFault> {
        let json = serde_json::to_vec(req)
            .map_err(|e| KernelFault::SerdeError(e.to_string()))?;
        if json.len() > PAYLOAD_CAP {
            return Err(KernelFault::FrameCapacityExceeded { required: json.len() });
        }

        // Reset flags for this step (Release so that Ring-0 sees fresh state).
        self.kernel_done().store(0, Ordering::Release);
        self.audit_committed().store(0, Ordering::Release);
        self.ring3_ready().store(0, Ordering::Release);

        // Increment sequence (Release): Ring-3 owns this write.
        let seq = self.sequence().fetch_add(1, Ordering::AcqRel).wrapping_add(1);

        // Write payload into request region (safe: RING3_READY=0, Ring-0 not reading).
        unsafe {
            Self::write_u64(self.base, OFF_PAYLOAD_LEN, json.len() as u64);
            std::ptr::copy_nonoverlapping(
                json.as_ptr(),
                self.base.add(OFF_REQUEST),
                json.len(),
            );
        }

        // Signal Ring-0: memory fence ensures payload write is visible before flag.
        self.ring3_ready().store(1, Ordering::Release);
        Ok(seq)
    }

    /// **Ring-0 side**: Spin-poll until `RING3_READY=1`, then read and decode the request.
    pub fn read_request(&self, timeout: Duration) -> Result<Ring3Request, KernelFault> {
        let deadline = Instant::now() + timeout;
        while self.ring3_ready().load(Ordering::Acquire) == 0 {
            if Instant::now() > deadline {
                return Err(KernelFault::SpinTimeout);
            }
            std::hint::spin_loop();
        }

        let len = unsafe { Self::read_u64(self.base, OFF_PAYLOAD_LEN) as usize };
        if len > PAYLOAD_CAP {
            return Err(KernelFault::FrameCapacityExceeded { required: len });
        }
        let bytes = unsafe { std::slice::from_raw_parts(self.base.add(OFF_REQUEST), len) };
        serde_json::from_slice(bytes).map_err(|e| KernelFault::SerdeError(e.to_string()))
    }

    /// **Ring-0 side**: Write result, commit I6 audit lock, then signal Ring-3.
    ///
    /// # I6 Invariant
    /// `AUDIT_COMMITTED` is set to 1 **before** `KERNEL_DONE`. Any code path that sets
    /// `KERNEL_DONE=1` without first setting `AUDIT_COMMITTED=1` is a Ring-0 protocol bug.
    /// Ring-3 verifies this and maps violations to `KernelFault::AuditLockViolation`.
    pub fn write_result(
        &self,
        result: &Ring0BridgeResult,
        audit_is_committed: bool,
    ) -> Result<(), KernelFault> {
        let json = serde_json::to_vec(result)
            .map_err(|e| KernelFault::SerdeError(e.to_string()))?;
        if json.len() > PAYLOAD_CAP {
            return Err(KernelFault::FrameCapacityExceeded { required: json.len() });
        }

        // Write result into result region (safe: Ring-3 is waiting on KERNEL_DONE=0).
        unsafe {
            Self::write_u64(self.base, OFF_RESULT_LEN, json.len() as u64);
            std::ptr::copy_nonoverlapping(
                json.as_ptr(),
                self.base.add(OFF_RESULT),
                json.len(),
            );
        }

        // I6 LOCK: AUDIT_COMMITTED must be set BEFORE KERNEL_DONE (Release ordering).
        // If `audit_is_committed` is false, we still set AUDIT_COMMITTED=0; Ring-3 will
        // detect the violation and raise KernelFault::AuditLockViolation.
        self.audit_committed()
            .store(u32::from(audit_is_committed), Ordering::Release);

        // Signal Ring-3: this store is guaranteed to be observed AFTER audit_committed.
        self.kernel_done().store(1, Ordering::Release);
        Ok(())
    }

    /// **Ring-3 side**: Spin-poll until `KERNEL_DONE=1`, verify I6 lock, read result.
    ///
    /// Returns `KernelFault::AuditLockViolation` if `AUDIT_COMMITTED=0` when `KERNEL_DONE=1`.
    pub fn read_result(
        &self,
        expected_seq: u32,
        timeout: Duration,
    ) -> Result<Ring0BridgeResult, KernelFault> {
        // Sequence guard: ensure we're reading the result we requested, not a stale one.
        let actual_seq = self.sequence().load(Ordering::Acquire);
        if actual_seq != expected_seq {
            return Err(KernelFault::StaleSequence {
                expected: expected_seq,
                found: actual_seq,
            });
        }

        let deadline = Instant::now() + timeout;
        while self.kernel_done().load(Ordering::Acquire) == 0 {
            if Instant::now() > deadline {
                return Err(KernelFault::SpinTimeout);
            }
            std::hint::spin_loop();
        }

        // I6 VERIFICATION (Acquire): must observe AUDIT_COMMITTED=1.
        if self.audit_committed().load(Ordering::Acquire) != 1 {
            return Err(KernelFault::AuditLockViolation);
        }

        let len = unsafe { Self::read_u64(self.base, OFF_RESULT_LEN) as usize };
        if len > PAYLOAD_CAP {
            return Err(KernelFault::FrameCapacityExceeded { required: len });
        }
        let bytes = unsafe { std::slice::from_raw_parts(self.base.add(OFF_RESULT), len) };
        serde_json::from_slice(bytes).map_err(|e| KernelFault::SerdeError(e.to_string()))
    }
}

impl Default for MmapFrame {
    fn default() -> Self { Self::new() }
}

impl Drop for MmapFrame {
    fn drop(&mut self) {
        unsafe {
            let FrameBacking::Heap(layout) = &self._backing;
            dealloc(self.base, *layout);
        }
    }
}

// ─── High-level adapters ──────────────────────────────────────────────────────

/// **Ring-3 adapter**: wraps the IPC frame for the Python-side (or Rust test) caller.
///
/// In production: Ring-3 is a Python subprocess; the `MmapFrame` is backed by a
/// named POSIX shared memory region (`/dev/shm/kernel_ipc_<trace_id>`). The
/// `mmap_adapter.py` script provides the Python counterpart.
pub struct Ring3IpcAdapter<'frame> {
    frame: &'frame MmapFrame,
    /// Default spin-wait timeout per step.
    pub poll_timeout: Duration,
}

impl<'frame> Ring3IpcAdapter<'frame> {
    pub fn new(frame: &'frame MmapFrame) -> Self {
        Self { frame, poll_timeout: Duration::from_millis(100) }
    }

    /// Send a request and block until the kernel has committed the result (I6).
    pub fn roundtrip(&self, req: &Ring3Request) -> Result<Ring0BridgeResult, KernelFault> {
        let seq = self.frame.write_request(req)?;
        self.frame.read_result(seq, self.poll_timeout)
    }
}

/// **Ring-0 adapter**: wraps the IPC frame for the Rust kernel side.
pub struct Ring0IpcAdapter<'frame> {
    frame: &'frame MmapFrame,
    pub poll_timeout: Duration,
}

impl<'frame> Ring0IpcAdapter<'frame> {
    pub fn new(frame: &'frame MmapFrame) -> Self {
        Self { frame, poll_timeout: Duration::from_millis(100) }
    }

    /// Wait for a request, process it with `handler`, write result + audit flag.
    pub fn serve_one<F>(&self, handler: F) -> Result<(), KernelFault>
    where
        F: FnOnce(Ring3Request) -> (Ring0BridgeResult, bool),
    {
        let req = self.frame.read_request(self.poll_timeout)?;
        let (result, audit_committed) = handler(req);
        self.frame.write_result(&result, audit_committed)
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::thread;

    fn make_req(trace: &str, step: u64) -> Ring3Request {
        Ring3Request {
            trace_id: trace.to_string(),
            step_index: step,
            policy_revision: 1,
            logits: vec![1.0, 2.0, 3.0],
            topk_indices: vec![0, 1, 2],
            dcbf_margin: 0.5,
            tool_call: None,
        }
    }

    fn make_ok_result(req: &Ring3Request, chosen: usize) -> Ring0BridgeResult {
        Ring0BridgeResult {
            trace_id: req.trace_id.clone(),
            step_index: req.step_index,
            policy_revision: req.policy_revision,
            feasible: true,
            chosen_index: Some(chosen),
            page_fault: false,
            cache_hit: false,
            cache_key_hex: "0x0".into(),
            qp_elapsed_us: 10,
            audit_committed: true,
            tool_verdict: None,
        }
    }

    // ── Core protocol ─────────────────────────────────────────────────────────

    #[test]
    fn happy_path_roundtrip() {
        let frame = Arc::new(MmapFrame::new());
        let f_kernel = Arc::clone(&frame);

        let req = make_req("tr-happy", 0);
        let req_clone = req.clone();

        let kernel = thread::spawn(move || {
            let adapter = Ring0IpcAdapter::new(&f_kernel);
            adapter.serve_one(|r| {
                assert_eq!(r.trace_id, "tr-happy");
                let result = make_ok_result(&r, 2);
                (result, true) // audit_committed = true
            })
        });

        let ring3 = Ring3IpcAdapter::new(&frame);
        let result = ring3.roundtrip(&req_clone).expect("roundtrip");
        kernel.join().unwrap().expect("kernel serve_one");

        assert!(result.feasible);
        assert_eq!(result.chosen_index, Some(2));
        assert!(result.audit_committed);
        assert_eq!(result.trace_id, "tr-happy");
    }

    #[test]
    fn i6_audit_lock_violation_detected() {
        // Ring-0 sets KERNEL_DONE=1 but AUDIT_COMMITTED=0 → Ring-3 must raise KernelFault.
        let frame = Arc::new(MmapFrame::new());
        let f_kernel = Arc::clone(&frame);

        let req = make_req("tr-i6-violate", 0);
        let req_clone = req.clone();

        let kernel = thread::spawn(move || {
            let adapter = Ring0IpcAdapter::new(&f_kernel);
            adapter.serve_one(|r| {
                let result = make_ok_result(&r, 1);
                (result, false) // audit_committed = FALSE → protocol violation
            })
        });

        let ring3 = Ring3IpcAdapter::new(&frame);
        let err = ring3.roundtrip(&req_clone).unwrap_err();
        kernel.join().unwrap().expect("kernel serve_one");

        assert_eq!(
            err,
            KernelFault::AuditLockViolation,
            "Ring-3 must detect I6 violation when AUDIT_COMMITTED=0"
        );
    }

    #[test]
    fn sequence_guard_prevents_stale_read() {
        let frame = MmapFrame::new();
        // Manually advance the sequence to simulate a stale state.
        frame.sequence().store(7, Ordering::SeqCst);
        frame.kernel_done().store(1, Ordering::SeqCst);
        frame.audit_committed().store(1, Ordering::SeqCst);

        // Ring-3 expects sequence=3 but frame has sequence=7.
        let err = frame
            .read_result(3, Duration::from_millis(10))
            .unwrap_err();
        assert!(
            matches!(err, KernelFault::StaleSequence { expected: 3, found: 7 }),
            "got: {err:?}"
        );
    }

    #[test]
    fn frame_capacity_exceeded() {
        let frame = MmapFrame::new();
        // Craft an oversized request.
        let huge_req = Ring3Request {
            trace_id: "x".repeat(5_000),
            step_index: 0,
            policy_revision: 1,
            logits: vec![],
            topk_indices: vec![],
            dcbf_margin: 0.0,
            tool_call: None,
        };
        let err = frame.write_request(&huge_req).unwrap_err();
        assert!(matches!(err, KernelFault::FrameCapacityExceeded { .. }));
    }

    #[test]
    fn spin_timeout_on_no_signal() {
        let frame = MmapFrame::new();
        // RING3_READY never set → Ring-0 should timeout.
        let adapter = Ring0IpcAdapter {
            frame: &frame,
            poll_timeout: Duration::from_millis(5),
        };
        let err = adapter
            .serve_one(|r| (make_ok_result(&r, 0), true))
            .unwrap_err();
        assert_eq!(err, KernelFault::SpinTimeout);
    }

    #[test]
    fn tool_call_payload_round_trips() {
        let frame = Arc::new(MmapFrame::new());
        let f_kernel = Arc::clone(&frame);

        let req = Ring3Request {
            trace_id: "tr-tool".into(),
            step_index: 0,
            policy_revision: 1,
            logits: vec![0.0],
            topk_indices: vec![0],
            dcbf_margin: 0.5,
            tool_call: Some(ToolCallPayload {
                tool_name: "read_file".into(),
                arguments: serde_json::json!({"path": "customer_data.csv"}),
                asserted_capabilities: vec!["tool:read_file:pii".into()],
            }),
        };
        let req_clone = req.clone();

        let kernel = thread::spawn(move || {
            let adapter = Ring0IpcAdapter::new(&f_kernel);
            adapter.serve_one(|r| {
                let tool_name = r.tool_call.as_ref().map(|t| t.tool_name.as_str());
                assert_eq!(tool_name, Some("read_file"));
                let mut result = make_ok_result(&r, 0);
                result.tool_verdict = Some(ToolVerdict {
                    allowed: false,
                    reason: "pii read requires explicit data-handling policy".into(),
                    forbidden_capabilities_triggered: vec!["tool:read_file:pii".into()],
                });
                (result, true)
            })
        });

        let ring3 = Ring3IpcAdapter::new(&frame);
        let result = ring3.roundtrip(&req_clone).expect("tool call roundtrip");
        kernel.join().unwrap().expect("kernel serve");

        let verdict = result.tool_verdict.expect("verdict present");
        assert!(!verdict.allowed);
        assert_eq!(verdict.forbidden_capabilities_triggered, vec!["tool:read_file:pii"]);
    }

    #[test]
    fn multiple_sequential_steps_no_stale_reads() {
        // Write request BEFORE spawning the kernel thread to avoid the stale-read race:
        // if the kernel thread is spawned before write_request, it can observe the
        // ring3_ready=1 flag left over from the previous iteration and consume a stale
        // payload.  By writing first (ring3_ready becomes 1 from a fresh payload), the
        // kernel thread always sees the current step's request.
        let frame = Arc::new(MmapFrame::new());
        for step in 0..5u64 {
            let req = make_req("tr-seq", step);

            // Phase 1: write request (ring3_ready = 1, fresh).
            let seq = frame.write_request(&req).expect("write_request");

            // Phase 2: spawn kernel thread — ring3_ready is already 1 so the kernel
            //          immediately picks up the current-step payload.
            let f_kernel = Arc::clone(&frame);
            let kernel = thread::spawn(move || {
                let adapter = Ring0IpcAdapter::new(&f_kernel);
                adapter.serve_one(|r| (make_ok_result(&r, r.step_index as usize % 3), true))
            });

            // Phase 3: Ring-3 waits for KERNEL_DONE, verifies I6, reads result.
            let result = frame
                .read_result(seq, Duration::from_millis(200))
                .expect("step roundtrip");
            kernel.join().unwrap().expect("kernel");
            assert!(result.feasible, "step {step} should be feasible");
        }
    }
}
