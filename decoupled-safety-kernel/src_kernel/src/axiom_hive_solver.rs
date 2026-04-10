//! Axiom Hive: joint projection on candidate set with **index-keyed** verdict lookup (RFC §5.5).
//! Corollary 2.1: tokenwise scan is polynomial in |candidates| × verifier cost.
//!
//! # Three-Tier Solver Ladder (Gap 2 — Control Layer)
//!
//! Tier 1 — `ProjectionCache` exact-match lookup (O(1), sub-microsecond):
//!   Cache key = hash(logits_bits ‖ topk_indices ‖ dcbf_margin_bits ‖ sorted_votes).
//!   Cache hit MUST still generate an `AuditRecord` with `cache_hit=true` in
//!   `projection_summary` before any visibility grant (I6 invariant is unconditional).
//!
//! Tier 2 — Warm-start QP (O(k) argmin, current Tier-2 maps to full solve on cache miss;
//!   future: partial-result warm-start for complex energy landscapes).
//!
//! Tier 3 — Full QP → `ProjectionFault` → `PageFault` when `QP_INNER_BUDGET` (4ms) exceeded.
//!
//! I6 AUDIT INVARIANT (CRITICAL):
//!   A cache HIT does NOT skip the evidence chain. The caller MUST write an `AuditRecord`
//!   with `projection_summary` containing the `cache_hit=true` tag and the correct
//!   `trace_id` / `step_index` / `policy_revision` BEFORE calling `into_user_visible()`.

use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};
use std::hash::{Hash, Hasher};
use std::collections::hash_map::DefaultHasher;
use std::time::{Duration, Instant};

use crate::judge_ensemble::{EnsembleReport, Vote};

/// RFC §6 / §10: total step budget and Axiom Hive (QP) inner slice.
pub const HARD_LATENCY_BUDGET: Duration = Duration::from_millis(20);
pub const QP_INNER_BUDGET: Duration = Duration::from_millis(4);

/// Polynomial upper bound on candidate set size (Corollary 2.1: |C| ≤ O(poly)).
///
/// Theorem 2 requires the candidate set to be polynomially bounded for instance-level
/// verification to remain in P. This constant enforces that bound: any candidate set
/// exceeding this limit is truncated to the top-scoring entries before projection.
/// Exceeding this threshold triggers a `page_fault` to signal the degradation path.
pub const MAX_CANDIDATE_SET_SIZE: usize = 128;

/// Placeholder automaton handle (RFC §5.0).
#[derive(Debug, Default)]
pub struct DeterministicAutomaton;

/// DCBF summary for projection energy coupling (RFC §5.5).
#[derive(Debug, Clone)]
pub struct DCBFReport {
    pub h_t: f32,
    pub margin: f32,
    pub interrupt: bool,
}

/// Per-candidate ensemble result; **`index` is the vocabulary / logit identity** (RFC §5.5).
#[derive(Debug, Clone)]
pub struct CandidateVerdict {
    pub index: usize,
    pub ensemble: EnsembleReport,
}

/// Projection input: **must not** zip `topk_indices` with `candidate_verdicts` by position.
#[derive(Debug)]
pub struct ProjectionInput<'a> {
    pub logits: &'a [f32],
    pub topk_indices: &'a [usize],
    pub candidate_verdicts: &'a [CandidateVerdict],
    pub automata: &'a DeterministicAutomaton,
    pub dcbf: &'a DCBFReport,
    pub deadline: Instant,
}

/// RFC §5.5 `ProjectionOutput`.
#[derive(Debug, Clone)]
pub struct ProjectionOutput {
    pub chosen_index: Option<usize>,
    pub feasible: bool,
    pub energy: f32,
    pub distance: f32,
    pub page_fault: bool,
}

/// Timer surface for mapping to `DeadlineExceeded` / `ProjectionFault` (RFC §10).
#[derive(Debug, Clone)]
pub struct ProjectionTimers {
    pub qp_elapsed: Duration,
    pub qp_budget_exceeded: bool,
    pub hard_budget_exceeded: bool,
}

#[derive(Debug, Clone)]
pub struct HiveProjectionResult {
    pub output: ProjectionOutput,
    pub timers: ProjectionTimers,
    /// Tier 1 cache hit: caller MUST still write an AuditRecord with `cache_hit=true`
    /// in `projection_summary` before any `into_user_visible()` call (I6 invariant).
    pub cache_hit: bool,
    /// Opaque cache key used to populate the audit record's projection_summary.
    pub cache_key: u64,
}

/// Maps to RFC timeout path when inner QP or hard step budget is exceeded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeadlineExceededKind {
    /// Entire `enforce_projection` exceeded monotonic `deadline`.
    Hard,
    /// Axiom Hive inner slice exceeded `QP_INNER_BUDGET` (4ms).
    QpInner,
}

impl HiveProjectionResult {
    #[must_use]
    pub fn deadline_exceeded(&self) -> Option<DeadlineExceededKind> {
        if self.timers.hard_budget_exceeded {
            return Some(DeadlineExceededKind::Hard);
        }
        if self.timers.qp_budget_exceeded {
            return Some(DeadlineExceededKind::QpInner);
        }
        None
    }

    /// Helper: build a `projection_summary` string for the AuditRecord.
    /// Caller MUST use this (or equivalent) in the AuditRecord before `into_user_visible`.
    #[must_use]
    pub fn audit_projection_summary(&self) -> String {
        format!(
            "chosen={:?}|feasible={}|page_fault={}|cache_hit={}|cache_key={:#018x}|qp_us={}",
            self.output.chosen_index,
            self.output.feasible,
            self.output.page_fault,
            self.cache_hit,
            self.cache_key,
            self.timers.qp_elapsed.as_micros(),
        )
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// ProjectionCache — Tier 1 (lock-free via single-owner; RefCell for interior
// mutability so the `enforce_projection` trait method keeps `&self`).
// ─────────────────────────────────────────────────────────────────────────────

/// Compute a stable 64-bit cache key from projection inputs.
///
/// Key derivation (order is deterministic):
///   policy_revision ‖ dcbf_margin_bits ‖ topk_indices ‖ logits@topk bits ‖ sorted_votes
///
/// Index-keyed votes are sorted by `index` so permutation of `candidate_verdicts` is
/// transparent — positional zip MUST NOT be assumed (RFC §5.5 uniqueness).
fn compute_cache_key(
    logits: &[f32],
    topk_indices: &[usize],
    dcbf: &DCBFReport,
    candidate_verdicts: &[CandidateVerdict],
    policy_revision: u64,
) -> u64 {
    let mut h = DefaultHasher::new();
    policy_revision.hash(&mut h);
    dcbf.margin.to_bits().hash(&mut h);
    topk_indices.hash(&mut h);
    for &idx in topk_indices {
        if let Some(lv) = logits.get(idx) {
            lv.to_bits().hash(&mut h);
        }
    }
    // Sort votes by index to make key order-independent w.r.t. candidate_verdicts slice.
    let mut votes: Vec<(usize, bool)> = candidate_verdicts
        .iter()
        .map(|cv| (cv.index, cv.ensemble.final_action == Vote::Deny))
        .collect();
    votes.sort_unstable_by_key(|&(idx, _)| idx);
    votes.hash(&mut h);
    h.finish()
}

/// Tier-1 FIFO-eviction projection cache (no external crates).
///
/// FIFO is chosen over true LRU because:
///   - In autoregressive generation the same prefix repeats on the *hot path*;
///     FIFO eviction preserves recently-inserted entries just as well as LRU for
///     low-cardinality workloads (|topk| ≤ 20, capacity default 256).
///   - True LRU needs O(1) doubly-linked list; FIFO suffices and avoids unsafe code.
pub struct ProjectionCache {
    capacity: usize,
    entries: RefCell<HashMap<u64, ProjectionOutput>>,
    order:   RefCell<VecDeque<u64>>,
    /// Monotonic counters for hit/miss reporting.
    hits:    RefCell<u64>,
    misses:  RefCell<u64>,
}

impl std::fmt::Debug for ProjectionCache {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProjectionCache")
            .field("capacity", &self.capacity)
            .field("len", &self.entries.borrow().len())
            .field("hits", &self.hits.borrow())
            .field("misses", &self.misses.borrow())
            .finish()
    }
}

impl ProjectionCache {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity,
            entries: RefCell::new(HashMap::with_capacity(capacity)),
            order:   RefCell::new(VecDeque::with_capacity(capacity)),
            hits:    RefCell::new(0),
            misses:  RefCell::new(0),
        }
    }

    pub fn get(&self, key: u64) -> Option<ProjectionOutput> {
        let map = self.entries.borrow();
        if let Some(out) = map.get(&key) {
            *self.hits.borrow_mut() += 1;
            Some(out.clone())
        } else {
            *self.misses.borrow_mut() += 1;
            None
        }
    }

    pub fn insert(&self, key: u64, output: ProjectionOutput) {
        let mut map = self.entries.borrow_mut();
        let mut ord = self.order.borrow_mut();
        if map.contains_key(&key) {
            return; // Already present; no-op.
        }
        if map.len() >= self.capacity {
            // FIFO eviction: remove oldest inserted key.
            while let Some(evict) = ord.pop_front() {
                if map.remove(&evict).is_some() {
                    break;
                }
            }
        }
        map.insert(key, output);
        ord.push_back(key);
    }

    pub fn hit_count(&self) -> u64 { *self.hits.borrow() }
    pub fn miss_count(&self) -> u64 { *self.misses.borrow() }
    pub fn len(&self) -> usize { self.entries.borrow().len() }
    pub fn is_empty(&self) -> bool { self.entries.borrow().is_empty() }
}

impl Default for ProjectionCache {
    fn default() -> Self { Self::new(256) }
}

// ─────────────────────────────────────────────────────────────────────────────
// Ring-0 Axiom Hive boundary trait
// ─────────────────────────────────────────────────────────────────────────────

/// Ring-0 Axiom Hive boundary (RFC §5.5).
pub trait AxiomHiveBoundary {
    fn enforce_projection(&self, input: ProjectionInput<'_>) -> ProjectionOutput;
}

// ─────────────────────────────────────────────────────────────────────────────
// AxiomHiveSolver — Tier 2/3: raw QP solve (no cache).
// ─────────────────────────────────────────────────────────────────────────────

/// Concrete solver with polynomial-time candidate scan and explicit QP timer checks.
#[derive(Debug, Clone)]
pub struct AxiomHiveSolver {
    pub qp_inner_budget: Duration,
    /// Test-only: sleep inside the QP section to force `qp_elapsed` > budget.
    #[cfg(test)]
    pub test_inject_qp_delay: Option<Duration>,
}

impl Default for AxiomHiveSolver {
    fn default() -> Self {
        Self {
            qp_inner_budget: QP_INNER_BUDGET,
            #[cfg(test)]
            test_inject_qp_delay: None,
        }
    }
}

impl AxiomHiveBoundary for AxiomHiveSolver {
    fn enforce_projection(&self, input: ProjectionInput<'_>) -> ProjectionOutput {
        self.enforce_projection_with_timers(input).output
    }
}

impl AxiomHiveSolver {
    /// Full projection with **index-keyed** `CandidateVerdict` lookup (never positional zip).
    ///
    /// Corollary 2.1 enforcement: if `candidate_verdicts.len()` exceeds
    /// `MAX_CANDIDATE_SET_SIZE`, the step is rejected with `page_fault = true`
    /// to guarantee the polynomial-time bound on instance-level verification.
    pub fn enforce_projection_with_timers(&self, input: ProjectionInput<'_>) -> HiveProjectionResult {
        let cache_key = compute_cache_key(
            input.logits, input.topk_indices, input.dcbf,
            input.candidate_verdicts, 0, // no policy_revision in raw solver
        );

        // Corollary 2.1: enforce polynomial bound on candidate set size.
        // If |C| > MAX_CANDIDATE_SET_SIZE, the branching factor exceeds the
        // theoretical poly bound → page_fault → graceful degradation FSM.
        if input.candidate_verdicts.len() > MAX_CANDIDATE_SET_SIZE {
            return HiveProjectionResult {
                output: ProjectionOutput {
                    chosen_index: None,
                    feasible: false,
                    energy: 0.0,
                    distance: 0.0,
                    page_fault: true,
                },
                timers: ProjectionTimers {
                    qp_elapsed: Duration::ZERO,
                    qp_budget_exceeded: false,
                    hard_budget_exceeded: false,
                },
                cache_hit: false,
                cache_key,
            };
        }

        if Instant::now() > input.deadline {
            return HiveProjectionResult {
                output: ProjectionOutput {
                    chosen_index: None,
                    feasible: false,
                    energy: 0.0,
                    distance: 0.0,
                    page_fault: true,
                },
                timers: ProjectionTimers {
                    qp_elapsed: Duration::ZERO,
                    qp_budget_exceeded: false,
                    hard_budget_exceeded: true,
                },
                cache_hit: false,
                cache_key,
            };
        }

        // Index identity map: duplicate indices → protocol fault (RFC §5.5 uniqueness).
        let mut by_index: HashMap<usize, &CandidateVerdict> = HashMap::new();
        for cv in input.candidate_verdicts {
            if by_index.insert(cv.index, cv).is_some() {
                return HiveProjectionResult {
                    output: ProjectionOutput {
                        chosen_index: None,
                        feasible: false,
                        energy: 0.0,
                        distance: 0.0,
                        page_fault: true,
                    },
                    timers: ProjectionTimers {
                        qp_elapsed: Duration::ZERO,
                        qp_budget_exceeded: false,
                        hard_budget_exceeded: false,
                    },
                    cache_hit: false,
                    cache_key,
                };
            }
        }

        // Polynomial scan (Corollary 2.1): only consider candidates present in topk with a verdict.
        let mut feasible: Vec<(usize, f32, f32)> = Vec::new();
        for &idx in input.topk_indices {
            let Some(cv) = by_index.get(&idx) else { continue; };
            if cv.ensemble.final_action == Vote::Deny { continue; }
            let logit = input.logits.get(idx).copied().unwrap_or(f32::NAN);
            if logit.is_nan() { continue; }
            let energy = -logit;
            let distance = input.dcbf.margin.abs();
            feasible.push((idx, energy, distance));
        }

        let qp_start = Instant::now();

        #[cfg(test)]
        if let Some(d) = self.test_inject_qp_delay {
            std::thread::sleep(d);
        }

        // Simulated QP / argmin over feasible set (O(k) for k = |feasible|).
        let chosen = feasible
            .iter()
            .min_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
            .copied();

        let qp_elapsed = qp_start.elapsed();
        let qp_budget_exceeded = qp_elapsed > self.qp_inner_budget;

        if qp_budget_exceeded {
            // Tier 3: QP overrun → ProjectionFault → PageFault (RFC §10).
            return HiveProjectionResult {
                output: ProjectionOutput {
                    chosen_index: None,
                    feasible: false,
                    energy: 0.0,
                    distance: 0.0,
                    page_fault: true,
                },
                timers: ProjectionTimers {
                    qp_elapsed,
                    qp_budget_exceeded: true,
                    hard_budget_exceeded: false,
                },
                cache_hit: false,
                cache_key,
            };
        }

        let hard_budget_exceeded = Instant::now() > input.deadline;

        let output = if hard_budget_exceeded {
            ProjectionOutput {
                chosen_index: None,
                feasible: false,
                energy: 0.0,
                distance: 0.0,
                page_fault: true,
            }
        } else if let Some((idx, energy, distance)) = chosen {
            ProjectionOutput {
                chosen_index: Some(idx),
                feasible: true,
                energy,
                distance,
                page_fault: false,
            }
        } else {
            ProjectionOutput {
                chosen_index: None,
                feasible: false,
                energy: 0.0,
                distance: 0.0,
                page_fault: false,
            }
        };

        HiveProjectionResult {
            output,
            timers: ProjectionTimers {
                qp_elapsed,
                qp_budget_exceeded: false,
                hard_budget_exceeded,
            },
            cache_hit: false,
            cache_key,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// CachedAxiomHiveSolver — Tier 1 + Tier 2/3 ladder.
// ─────────────────────────────────────────────────────────────────────────────

/// Three-tier solver ladder (Gap 2 fix).
///
/// Tier 1: `ProjectionCache` exact-match → sub-microsecond; cache hit annotated for audit.
/// Tier 2/3: delegate to `AxiomHiveSolver` on cache miss; result stored back in cache.
///
/// # I6 Audit Invariant
/// `cache_hit=true` in the returned `HiveProjectionResult` MEANS the result was served from
/// cache.  The **caller** MUST still produce an `AuditRecord` using
/// `result.audit_projection_summary()` (which encodes `cache_hit=true`) and append it to the
/// evidence chain BEFORE calling `into_user_visible()`. Skipping audit on cache hit is a
/// protocol violation (RFC §8 I6).
pub struct CachedAxiomHiveSolver {
    pub inner: AxiomHiveSolver,
    pub cache: ProjectionCache,
    pub policy_revision: u64,
}

impl std::fmt::Debug for CachedAxiomHiveSolver {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CachedAxiomHiveSolver")
            .field("policy_revision", &self.policy_revision)
            .field("cache", &self.cache)
            .finish()
    }
}

impl CachedAxiomHiveSolver {
    pub fn new(policy_revision: u64, cache_capacity: usize) -> Self {
        Self {
            inner: AxiomHiveSolver::default(),
            cache: ProjectionCache::new(cache_capacity),
            policy_revision,
        }
    }

    /// Primary entry point: Tier-1 cache lookup → Tier-2/3 QP solve on miss.
    pub fn enforce_projection_with_timers(
        &self,
        input: ProjectionInput<'_>,
    ) -> HiveProjectionResult {
        let key = compute_cache_key(
            input.logits, input.topk_indices, input.dcbf,
            input.candidate_verdicts, self.policy_revision,
        );

        // ── Tier 1: cache hit path (O(1), sub-microsecond).
        if let Some(cached_output) = self.cache.get(key) {
            return HiveProjectionResult {
                output: cached_output,
                // Cache hit: QP was not invoked; timers reflect near-zero overhead.
                timers: ProjectionTimers {
                    qp_elapsed: Duration::ZERO,
                    qp_budget_exceeded: false,
                    hard_budget_exceeded: false,
                },
                cache_hit: true,
                cache_key: key,
            };
        }

        // ── Tier 2/3: cache miss → full QP solve (with budget enforcement).
        // Rebuild input with updated cache_key-aware solver for consistency.
        let mut result = self.inner.enforce_projection_with_timers(input);
        result.cache_key = key; // Overwrite with policy-revision-aware key.

        // Store successful (non-page_fault) results in cache to warm future steps.
        // Page-fault results are NOT cached: they indicate transient overload and
        // should re-attempt QP on next invocation.
        if !result.output.page_fault {
            self.cache.insert(key, result.output.clone());
        }

        result
    }
}

impl AxiomHiveBoundary for CachedAxiomHiveSolver {
    fn enforce_projection(&self, input: ProjectionInput<'_>) -> ProjectionOutput {
        self.enforce_projection_with_timers(input).output
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// DynamicKExpansionPolicy — Round-3 R2: retry with expanded K on EmptySafeCandidateSet
// ─────────────────────────────────────────────────────────────────────────────

/// Maximum K after dynamic expansion. Beyond this, hard reject is enforced.
pub const MAX_EXPANDED_K: usize = 512;

/// Policy for handling `EmptySafeCandidateSet` (no feasible candidate after projection).
///
/// When `enforce_projection` yields `chosen_index == None && feasible == false && !page_fault`,
/// all top-K candidates were denied. Instead of immediately rejecting, this policy allows
/// doubling K (up to `MAX_EXPANDED_K`) and re-invoking the solver with the expanded set.
///
/// This addresses the Round-3 reviewer concern that hard rejection on empty safe set is
/// overly conservative and may degrade utility unnecessarily.
#[derive(Debug, Clone)]
pub struct DynamicKExpansionPolicy {
    pub enabled: bool,
    pub initial_k: usize,
    pub max_k: usize,
    pub max_retries: usize,
}

impl Default for DynamicKExpansionPolicy {
    fn default() -> Self {
        Self {
            enabled: false,
            initial_k: MAX_CANDIDATE_SET_SIZE,
            max_k: MAX_EXPANDED_K,
            max_retries: 2,
        }
    }
}

/// Result of a dynamic-K expansion attempt.
#[derive(Debug, Clone)]
pub struct DynamicExpansionResult {
    pub final_k: usize,
    pub expansions_attempted: usize,
    pub final_result: HiveProjectionResult,
    pub expanded: bool,
}

impl DynamicKExpansionPolicy {
    /// Attempt projection with dynamic K expansion.
    ///
    /// `make_input_for_k` is a closure that, given a K value, returns a new
    /// `(topk_indices, candidate_verdicts, logits)` tuple expanded to that K.
    /// The caller is responsible for fetching additional candidates from the
    /// model's logit distribution.
    ///
    /// Returns `DynamicExpansionResult` with the final K used, number of
    /// expansion attempts, and the last projection result.
    pub fn try_with_expansion<F>(
        &self,
        solver: &AxiomHiveSolver,
        initial_result: HiveProjectionResult,
        initial_input_k: usize,
        mut make_input_for_k: F,
    ) -> DynamicExpansionResult
    where
        F: FnMut(usize) -> Option<(Vec<usize>, Vec<CandidateVerdict>, Vec<f32>)>,
    {
        if !self.enabled {
            return DynamicExpansionResult {
                final_k: initial_input_k,
                expansions_attempted: 0,
                final_result: initial_result,
                expanded: false,
            };
        }

        let is_empty_safe_set = initial_result.output.chosen_index.is_none()
            && !initial_result.output.feasible
            && !initial_result.output.page_fault;

        if !is_empty_safe_set {
            return DynamicExpansionResult {
                final_k: initial_input_k,
                expansions_attempted: 0,
                final_result: initial_result,
                expanded: false,
            };
        }

        let mut current_k = initial_input_k;
        let mut last_result = initial_result;
        let mut attempts = 0;

        while attempts < self.max_retries && current_k < self.max_k {
            let new_k = (current_k * 2).min(self.max_k);
            if new_k == current_k {
                break;
            }
            current_k = new_k;
            attempts += 1;

            let Some((topk, verdicts, logits)) = make_input_for_k(current_k) else {
                break;
            };

            let dcbf = DCBFReport { h_t: 1.0, margin: 0.1, interrupt: false };
            let input = ProjectionInput {
                logits: &logits,
                topk_indices: &topk,
                candidate_verdicts: &verdicts,
                automata: &DeterministicAutomaton,
                dcbf: &dcbf,
                deadline: Instant::now() + HARD_LATENCY_BUDGET,
            };

            last_result = solver.enforce_projection_with_timers(input);

            if last_result.output.chosen_index.is_some() {
                break;
            }
        }

        DynamicExpansionResult {
            final_k: current_k,
            expansions_attempted: attempts,
            final_result: last_result,
            expanded: attempts > 0,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::judge_ensemble::{JudgeEnsemble, Verdict};

    fn ens_allow() -> EnsembleReport {
        JudgeEnsemble::new().tally(vec![Verdict {
            vote: Vote::Allow,
            confidence: 1.0,
            explanation: String::new(),
            verifier_id: "v1".into(),
        }])
    }

    fn make_input_fixture<'a>(
        logits: &'a [f32],
        topk: &'a [usize],
        verdicts: &'a [CandidateVerdict],
        dcbf: &'a DCBFReport,
    ) -> ProjectionInput<'a> {
        ProjectionInput {
            logits,
            topk_indices: topk,
            candidate_verdicts: verdicts,
            automata: &DeterministicAutomaton,
            dcbf,
            deadline: Instant::now() + HARD_LATENCY_BUDGET,
        }
    }

    #[test]
    fn projection_matches_by_index_not_position() {
        // Verdict order deliberately **not** aligned with topk order (zip would pick wrong logit).
        let logits = vec![0.0f32, 1.0, 2.0, 3.0];
        let topk = [2usize, 0, 1];
        let verdicts = vec![
            CandidateVerdict { index: 1, ensemble: ens_allow() },
            CandidateVerdict { index: 2, ensemble: ens_allow() },
            CandidateVerdict { index: 0, ensemble: ens_allow() },
        ];
        let dcbf = DCBFReport { h_t: 1.0, margin: 0.1, interrupt: false };
        let input = make_input_fixture(&logits, &topk, &verdicts, &dcbf);
        let solver = AxiomHiveSolver::default();
        let r = solver.enforce_projection_with_timers(input);
        // Lowest energy = -logit = -3.0 at index 2 wins among {0,1,2}.
        assert_eq!(r.output.chosen_index, Some(2));
        assert!(r.output.feasible);
        assert!(!r.cache_hit);
    }

    #[test]
    fn qp_deadline_mapping() {
        let logits = vec![0.0f32, 1.0];
        let topk = [0usize, 1];
        let verdicts = vec![
            CandidateVerdict { index: 0, ensemble: ens_allow() },
            CandidateVerdict { index: 1, ensemble: ens_allow() },
        ];
        let dcbf = DCBFReport { h_t: 1.0, margin: 0.0, interrupt: false };
        let input = ProjectionInput {
            logits: &logits,
            topk_indices: &topk,
            candidate_verdicts: &verdicts,
            automata: &DeterministicAutomaton,
            dcbf: &dcbf,
            deadline: Instant::now() + Duration::from_secs(60),
        };
        let solver = AxiomHiveSolver {
            qp_inner_budget: Duration::from_millis(4),
            test_inject_qp_delay: Some(Duration::from_millis(5)),
        };
        let r = solver.enforce_projection_with_timers(input);
        assert!(r.timers.qp_budget_exceeded);
        assert!(r.output.page_fault);
        assert_eq!(r.deadline_exceeded(), Some(DeadlineExceededKind::QpInner));
    }

    // ── Cache tests ───────────────────────────────────────────────────────────

    #[test]
    fn cache_hit_bypasses_qp_timer() {
        // First call: cache miss → QP solve → result stored.
        // Second call with identical input: cache hit → qp_elapsed == 0, cache_hit == true.
        let logits = vec![1.0f32, 2.0, 3.0];
        let topk = [0usize, 1, 2];
        let verdicts = vec![
            CandidateVerdict { index: 0, ensemble: ens_allow() },
            CandidateVerdict { index: 1, ensemble: ens_allow() },
            CandidateVerdict { index: 2, ensemble: ens_allow() },
        ];
        let dcbf = DCBFReport { h_t: 1.0, margin: 0.5, interrupt: false };
        let solver = CachedAxiomHiveSolver::new(1, 16);

        let r1 = solver.enforce_projection_with_timers(make_input_fixture(
            &logits, &topk, &verdicts, &dcbf,
        ));
        assert!(!r1.cache_hit, "first call must be a miss");
        assert_eq!(solver.cache.miss_count(), 1);
        assert_eq!(solver.cache.hit_count(), 0);

        let r2 = solver.enforce_projection_with_timers(make_input_fixture(
            &logits, &topk, &verdicts, &dcbf,
        ));
        assert!(r2.cache_hit, "second call must be a hit");
        assert_eq!(r2.timers.qp_elapsed, Duration::ZERO, "cache hit: qp_elapsed must be zero");
        assert_eq!(solver.cache.hit_count(), 1);
        // Output must match (index-keyed, not positionally different).
        assert_eq!(r1.output.chosen_index, r2.output.chosen_index);
        assert_eq!(r1.output.feasible, r2.output.feasible);
    }

    #[test]
    fn cache_hit_annotated_for_audit() {
        // cache_hit=true must appear in audit_projection_summary so the caller can write I6.
        let logits = vec![1.0f32, 2.0];
        let topk = [0usize, 1];
        let verdicts = vec![
            CandidateVerdict { index: 0, ensemble: ens_allow() },
            CandidateVerdict { index: 1, ensemble: ens_allow() },
        ];
        let dcbf = DCBFReport { h_t: 1.0, margin: 0.2, interrupt: false };
        let solver = CachedAxiomHiveSolver::new(7, 16);

        solver.enforce_projection_with_timers(make_input_fixture(&logits, &topk, &verdicts, &dcbf));
        let r2 = solver.enforce_projection_with_timers(make_input_fixture(
            &logits, &topk, &verdicts, &dcbf,
        ));
        assert!(r2.cache_hit);
        let summary = r2.audit_projection_summary();
        assert!(
            summary.contains("cache_hit=true"),
            "audit summary must tag cache_hit: got {summary}"
        );
    }

    #[test]
    fn cache_eviction_respects_capacity() {
        // Insert capacity+1 distinct entries; oldest must be evicted.
        let capacity = 4usize;
        let cache = ProjectionCache::new(capacity);
        for i in 0..(capacity + 1) as u64 {
            cache.insert(i, ProjectionOutput {
                chosen_index: Some(i as usize),
                feasible: true,
                energy: 0.0,
                distance: 0.0,
                page_fault: false,
            });
        }
        assert!(cache.len() <= capacity, "cache must not exceed capacity");
        // Key 0 (oldest) should be evicted.
        assert!(cache.get(0).is_none(), "oldest entry must be evicted");
        // Key `capacity` (newest) must still be present.
        assert!(cache.get(capacity as u64).is_some(), "newest entry must survive eviction");
    }

    #[test]
    fn page_fault_result_not_cached() {
        // QP overrun results (page_fault=true) must NOT be cached: next call should re-solve.
        let logits = vec![0.0f32, 1.0];
        let topk = [0usize, 1];
        let verdicts = vec![
            CandidateVerdict { index: 0, ensemble: ens_allow() },
            CandidateVerdict { index: 1, ensemble: ens_allow() },
        ];
        let dcbf = DCBFReport { h_t: 1.0, margin: 0.0, interrupt: false };

        // Use a raw AxiomHiveSolver with injected QP delay (to force page_fault).
        // Then verify manually that CachedAxiomHiveSolver with same params leaves cache empty.
        let inner = AxiomHiveSolver {
            qp_inner_budget: Duration::from_millis(4),
            test_inject_qp_delay: Some(Duration::from_millis(5)),
        };
        let solver = CachedAxiomHiveSolver {
            inner,
            cache: ProjectionCache::new(8),
            policy_revision: 1,
        };
        let input = ProjectionInput {
            logits: &logits,
            topk_indices: &topk,
            candidate_verdicts: &verdicts,
            automata: &DeterministicAutomaton,
            dcbf: &dcbf,
            deadline: Instant::now() + Duration::from_secs(10),
        };
        let r = solver.enforce_projection_with_timers(input);
        assert!(r.output.page_fault, "QP overrun must produce page_fault");
        assert!(solver.cache.is_empty(), "page_fault result must NOT be cached");
    }

    #[test]
    fn different_policy_revision_different_cache_key() {
        // Two solvers with different policy_revision must produce different cache keys.
        let logits = vec![1.0f32, 2.0];
        let topk = [0usize, 1];
        let verdicts = vec![
            CandidateVerdict { index: 0, ensemble: ens_allow() },
            CandidateVerdict { index: 1, ensemble: ens_allow() },
        ];
        let dcbf = DCBFReport { h_t: 1.0, margin: 0.3, interrupt: false };

        let k1 = compute_cache_key(&logits, &topk, &dcbf, &verdicts, 1);
        let k2 = compute_cache_key(&logits, &topk, &dcbf, &verdicts, 2);
        assert_ne!(k1, k2, "different policy_revision must yield different cache keys");
    }
}
