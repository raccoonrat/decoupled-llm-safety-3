//! Graceful Degradation FSM: Corollary 3.7 (absorbing ⊥) and RFC §5.6 `DegradeAction`.
//!
//! When semilattice meet has no non-⊥ refinement, the algebra converges to the fail-safe bottom;
//! operationally this maps to explicit degrade transitions (Template / Refuse / Redact / Shutdown).

/// RFC §5.6 — terminal actions after fault routing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DegradeAction {
    EmitSafeTemplate,
    Refuse,
    Redact,
    Shutdown,
}

/// Reasons that MUST route through PageFault / ⊥ absorption paths (RFC §9.1, §5.5).
#[derive(Debug, Clone)]
pub enum PageFaultReason {
    /// `SafetyFault::EmptySafeCandidateSet` — no legal non-deny candidate.
    EmptySafeCandidateSet,
    /// Projection infeasible or `page_fault` from Axiom Hive.
    ProjectionFailure {
        feasible: bool,
        page_fault: bool,
    },
    /// Corollary 3.7 — irreducible policy conflict; semilattice meet is ⊥.
    AbsorbingBottom {
        /// Stable tag for audit (e.g. `algebraic_meet_empty`).
        reason: &'static str,
    },
    /// DCBF `interrupt` — latent barrier broken.
    DcbfInterrupt,
    /// Step budget exceeded (`DeadlineExceeded`).
    DeadlineExceeded,
    /// Audit append failed — fail-safe deny (RFC §0, §11.2).
    AuditAppendFailed,
}

/// Back-compat alias for earlier skeleton name.
pub type GracefulDegradation = GracefulDegradationFsm;

/// Default routing policy for Ring-0 (deployment may override via config wrapper).
#[derive(Debug, Clone, Default)]
pub struct DegradePolicy {
    /// When true, `AbsorbingBottom` maps to template instead of hard refuse (still audited).
    pub bottom_prefers_template: bool,
}

/// Graceful degradation finite-state controller (explicit transitions only).
#[derive(Debug, Clone, Default)]
pub struct GracefulDegradationFsm {
    pub policy: DegradePolicy,
}

impl GracefulDegradationFsm {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn with_policy(policy: DegradePolicy) -> Self {
        Self { policy }
    }

    /// Map faults to `DegradeAction`. `ProjectionFailure` with `feasible == false` or
    /// `page_fault == true` MUST use PageFault-style degradation (RFC §5.5 / §6).
    pub fn route(&self, reason: &PageFaultReason) -> DegradeAction {
        match reason {
            PageFaultReason::EmptySafeCandidateSet => DegradeAction::EmitSafeTemplate,
            PageFaultReason::ProjectionFailure { feasible, page_fault } => {
                if !*feasible || *page_fault {
                    DegradeAction::EmitSafeTemplate
                } else {
                    DegradeAction::Refuse
                }
            }
            PageFaultReason::AbsorbingBottom { .. } => {
                if self.policy.bottom_prefers_template {
                    DegradeAction::EmitSafeTemplate
                } else {
                    DegradeAction::Refuse
                }
            }
            PageFaultReason::DcbfInterrupt => DegradeAction::Refuse,
            PageFaultReason::DeadlineExceeded => DegradeAction::EmitSafeTemplate,
            PageFaultReason::AuditAppendFailed => DegradeAction::Refuse,
        }
    }

    /// Convenience: `EmptySafeCandidateSet` or projection not feasible → PageFault path.
    #[must_use]
    pub fn route_page_fault_token_step(
        empty_candidates: bool,
        projection_feasible: bool,
        projection_page_fault: bool,
    ) -> PageFaultReason {
        if empty_candidates {
            return PageFaultReason::EmptySafeCandidateSet;
        }
        PageFaultReason::ProjectionFailure {
            feasible: projection_feasible,
            page_fault: projection_page_fault,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_set_templates() {
        let fsm = GracefulDegradationFsm::new();
        assert_eq!(
            fsm.route(&PageFaultReason::EmptySafeCandidateSet),
            DegradeAction::EmitSafeTemplate
        );
    }

    #[test]
    fn infeasible_projection_templates() {
        let fsm = GracefulDegradationFsm::new();
        assert_eq!(
            fsm.route(&PageFaultReason::ProjectionFailure {
                feasible: false,
                page_fault: false,
            }),
            DegradeAction::EmitSafeTemplate
        );
    }

    #[test]
    fn corollary_3_7_absorbing_bottom_refuse_by_default() {
        let fsm = GracefulDegradationFsm::new();
        assert_eq!(
            fsm.route(&PageFaultReason::AbsorbingBottom {
                reason: "meet_empty",
            }),
            DegradeAction::Refuse
        );
    }
}
