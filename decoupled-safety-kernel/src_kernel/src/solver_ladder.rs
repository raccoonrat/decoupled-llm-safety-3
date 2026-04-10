//! Three-tier projection solver ladder (Gap 2 — Control Layer, RFC §5.5 / §10).
//!
//! | Tier | Component                   | Budget          | On overrun                |
//! |------|-----------------------------|-----------------|---------------------------|
//! | 1    | `ProjectionCache` hit       | ~0 µs           | n/a (always fast)         |
//! | 2    | `CachedAxiomHiveSolver` QP  | ≤ QP_INNER_BUDGET (4ms) | `ProjectionFault` → `PageFault` |
//! | 3    | `GracefulDegradationFsm`    | HARD_LATENCY (20ms) | `PageFault` → safe template |
//!
//! # I6 Invariant (unconditional)
//! ALL three tiers MUST produce an `AuditRecord` before `into_user_visible()`.
//! Cache hits annotate `projection_summary` with `cache_hit=true`; the audit record
//! MUST still be durably appended with the current `trace_id` and `step_index`.

pub use crate::axiom_hive_solver::{
    AxiomHiveBoundary, AxiomHiveSolver, CachedAxiomHiveSolver,
    DCBFReport, CandidateVerdict, DeterministicAutomaton,
    HiveProjectionResult, ProjectionCache, ProjectionInput, ProjectionOutput,
    ProjectionTimers, DeadlineExceededKind,
    HARD_LATENCY_BUDGET, QP_INNER_BUDGET,
};

/// Branch candidate scan budget for polynomial-time projection (aligns with `HARD_LATENCY_BUDGET`).
#[derive(Debug, Clone, Copy)]
pub struct SolverBudget {
    pub max_micros: u64,
}

impl Default for SolverBudget {
    fn default() -> Self {
        Self { max_micros: 20_000 }
    }
}

/// Convenience: build a cached solver with RFC-defaults and a given `policy_revision`.
pub fn build_cached_solver(policy_revision: u64) -> CachedAxiomHiveSolver {
    CachedAxiomHiveSolver::new(policy_revision, 256)
}
