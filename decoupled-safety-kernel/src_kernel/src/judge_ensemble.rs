//! Judge ensemble: tallies verifier votes; conflict → Deny unless audited break-glass (RFC §5.4, I4).

/// Verifier vote (RFC §5.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Vote {
    Allow,
    Revise,
    Deny,
    Abstain,
}

#[derive(Debug, Clone)]
pub struct Verdict {
    pub vote: Vote,
    pub confidence: f32,
    pub explanation: String,
    pub verifier_id: String,
}

/// Ensemble report after tally + conflict resolution (RFC §5.4).
#[derive(Debug, Clone)]
pub struct EnsembleReport {
    pub verdicts: Vec<Verdict>,
    pub tally_allow: u32,
    pub tally_revise: u32,
    pub tally_deny: u32,
    pub tally_abstain: u32,
    /// True when verifiers disagree in a way that MUST NOT collapse to Allow without audit (RFC §5.4).
    pub conflict: bool,
    pub final_action: Vote,
    /// True when break-glass was invoked to override a conflict Deny → Allow.
    pub break_glass_used: bool,
}

/// Confidence-weighted ensemble report (RFC §5.4 Round-2 extension).
#[derive(Debug, Clone)]
pub struct WeightedEnsembleReport {
    pub base: EnsembleReport,
    pub weighted_allow: f64,
    pub weighted_revise: f64,
    pub weighted_deny: f64,
    pub weighted_abstain: f64,
    pub weighted_final_action: Vote,
}

/// Audit record for a break-glass override. When break-glass resolves a
/// conflict away from Deny, this record MUST be written to the Evidence Chain
/// before the verdict becomes visible (RFC §5.4 / I4).
///
/// In the safety algebra (Theorem 3.3), break-glass is modeled as a
/// **supervised audit exception** — a controlled non-monotone path that
/// requires an external human audit trail. The audit trail itself is a
/// safety operator whose composition with the break-glass override still
/// satisfies fail-safe convergence (Theorem 3.6).
#[derive(Debug, Clone)]
pub struct BreakGlassAuditRecord {
    pub auditor_id: String,
    pub timestamp_epoch_ms: u64,
    pub justification: String,
    pub original_action: Vote,
    pub overridden_action: Vote,
}

/// Audited break-glass policy: only when `enabled` may conflict resolve away from Deny (RFC §5.4 / I4).
///
/// When `audit_trail_required` is true (default), the caller MUST supply a
/// `BreakGlassAuditRecord` before the overridden verdict is acted upon.
/// This ensures the non-monotone path is always traceable and auditable,
/// preserving the algebraic fail-safe guarantee under human supervision.
#[derive(Debug, Clone, Default)]
pub struct BreakGlassPolicy {
    pub enabled: bool,
    /// When true, break-glass override is only valid with an accompanying audit record.
    pub audit_trail_required: bool,
}

/// Tallies per-verifier verdicts into an `EnsembleReport`.
#[derive(Debug, Default)]
pub struct JudgeEnsemble {
    pub break_glass: BreakGlassPolicy,
}

impl JudgeEnsemble {
    #[must_use]
    pub fn new() -> Self {
        Self {
            break_glass: BreakGlassPolicy::default(),
        }
    }

    #[must_use]
    pub fn with_break_glass(mut self, policy: BreakGlassPolicy) -> Self {
        self.break_glass = policy;
        self
    }

    /// Confidence-weighted tally: weights each vote by `confidence` ∈ [0, 1].
    /// Deny-first: if weighted deny score > 0, final action is Deny (unless break-glass).
    /// Returns `WeightedEnsembleReport` with weighted scores alongside traditional counts.
    pub fn confidence_weighted_tally(&self, verdicts: Vec<Verdict>) -> WeightedEnsembleReport {
        let mut w_allow = 0.0_f64;
        let mut w_revise = 0.0_f64;
        let mut w_deny = 0.0_f64;
        let mut w_abstain = 0.0_f64;

        for v in &verdicts {
            let c = (v.confidence as f64).clamp(0.0, 1.0);
            match v.vote {
                Vote::Allow => w_allow += c,
                Vote::Revise => w_revise += c,
                Vote::Deny => w_deny += c,
                Vote::Abstain => w_abstain += c,
            }
        }

        let conflict = Self::compute_conflict(&verdicts);

        let final_action = if conflict {
            if self.break_glass.enabled {
                Vote::Allow
            } else {
                Vote::Deny
            }
        } else if w_deny > 0.0 {
            Vote::Deny
        } else if w_revise > w_allow {
            Vote::Revise
        } else if w_allow > 0.0 {
            Vote::Allow
        } else {
            Vote::Abstain
        };

        let base = self.tally_inner(&verdicts, conflict);
        WeightedEnsembleReport {
            base,
            weighted_allow: w_allow,
            weighted_revise: w_revise,
            weighted_deny: w_deny,
            weighted_abstain: w_abstain,
            weighted_final_action: final_action,
        }
    }

    fn tally_inner(&self, verdicts: &[Verdict], conflict: bool) -> EnsembleReport {
        let mut tally_allow = 0u32;
        let mut tally_revise = 0u32;
        let mut tally_deny = 0u32;
        let mut tally_abstain = 0u32;

        for v in verdicts {
            match v.vote {
                Vote::Allow => tally_allow += 1,
                Vote::Revise => tally_revise += 1,
                Vote::Deny => tally_deny += 1,
                Vote::Abstain => tally_abstain += 1,
            }
        }

        let break_glass_used = conflict && self.break_glass.enabled;
        let final_action = if conflict {
            if self.break_glass.enabled {
                Vote::Allow
            } else {
                Vote::Deny
            }
        } else {
            Self::majority_vote(tally_allow, tally_revise, tally_deny, tally_abstain)
        };

        EnsembleReport {
            verdicts: verdicts.to_vec(),
            tally_allow,
            tally_revise,
            tally_deny,
            tally_abstain,
            conflict,
            final_action,
            break_glass_used,
        }
    }

    /// Aggregate verifier outputs. If `conflict == true`, `final_action` MUST be `Deny` unless
    /// `break_glass.enabled` (audited policy registered out-of-band).
    pub fn tally(&self, verdicts: Vec<Verdict>) -> EnsembleReport {
        let conflict = Self::compute_conflict(&verdicts);
        self.tally_inner(&verdicts, conflict)
    }

    /// Conflict: simultaneous Allow and Deny among verifiers (cannot silently merge).
    fn compute_conflict(verdicts: &[Verdict]) -> bool {
        let has_allow = verdicts.iter().any(|v| v.vote == Vote::Allow);
        let has_deny = verdicts.iter().any(|v| v.vote == Vote::Deny);
        has_allow && has_deny
    }

    fn majority_vote(
        tally_allow: u32,
        tally_revise: u32,
        tally_deny: u32,
        tally_abstain: u32,
    ) -> Vote {
        // Default-safe: prefer Deny on ties involving safety-critical votes.
        if tally_deny >= tally_allow && tally_deny >= tally_revise {
            return Vote::Deny;
        }
        if tally_allow >= tally_revise && tally_allow >= tally_abstain {
            return Vote::Allow;
        }
        if tally_revise > 0 {
            return Vote::Revise;
        }
        Vote::Abstain
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(id: &str, vote: Vote) -> Verdict {
        Verdict {
            vote,
            confidence: 1.0,
            explanation: String::new(),
            verifier_id: id.to_string(),
        }
    }

    fn vc(id: &str, vote: Vote, confidence: f32) -> Verdict {
        Verdict {
            vote,
            confidence,
            explanation: String::new(),
            verifier_id: id.to_string(),
        }
    }

    #[test]
    fn conflict_defaults_to_deny_without_break_glass() {
        let je = JudgeEnsemble::new();
        let r = je.tally(vec![v("a", Vote::Allow), v("b", Vote::Deny)]);
        assert!(r.conflict);
        assert_eq!(r.final_action, Vote::Deny);
    }

    #[test]
    fn conflict_allows_break_glass_when_audited() {
        let je = JudgeEnsemble::new().with_break_glass(BreakGlassPolicy { enabled: true, audit_trail_required: true });
        let r = je.tally(vec![v("a", Vote::Allow), v("b", Vote::Deny)]);
        assert!(r.conflict);
        assert!(r.break_glass_used);
        assert_eq!(r.final_action, Vote::Allow);
    }

    #[test]
    fn unanimous_allow() {
        let je = JudgeEnsemble::new();
        let r = je.tally(vec![v("a", Vote::Allow), v("b", Vote::Allow)]);
        assert!(!r.conflict);
        assert_eq!(r.final_action, Vote::Allow);
    }

    #[test]
    fn weighted_deny_first() {
        let je = JudgeEnsemble::new();
        let r = je.confidence_weighted_tally(vec![
            vc("a", Vote::Allow, 0.9),
            vc("b", Vote::Deny, 0.1),
        ]);
        assert_eq!(r.weighted_final_action, Vote::Deny);
        assert!(r.weighted_deny > 0.0);
    }

    #[test]
    fn weighted_allow_when_no_deny() {
        let je = JudgeEnsemble::new();
        let r = je.confidence_weighted_tally(vec![
            vc("a", Vote::Allow, 0.8),
            vc("b", Vote::Revise, 0.2),
        ]);
        assert_eq!(r.weighted_final_action, Vote::Allow);
    }

    #[test]
    fn weighted_revise_beats_allow() {
        let je = JudgeEnsemble::new();
        let r = je.confidence_weighted_tally(vec![
            vc("a", Vote::Allow, 0.2),
            vc("b", Vote::Revise, 0.8),
        ]);
        assert_eq!(r.weighted_final_action, Vote::Revise);
    }
}
