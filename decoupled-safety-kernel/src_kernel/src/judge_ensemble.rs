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
}

/// Audited break-glass policy: only when `enabled` may conflict resolve away from Deny (RFC §5.4 / I4).
#[derive(Debug, Clone, Copy, Default)]
pub struct BreakGlassPolicy {
    pub enabled: bool,
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

    /// Aggregate verifier outputs. If `conflict == true`, `final_action` MUST be `Deny` unless
    /// `break_glass.enabled` (audited policy registered out-of-band).
    pub fn tally(&self, verdicts: Vec<Verdict>) -> EnsembleReport {
        let mut tally_allow = 0u32;
        let mut tally_revise = 0u32;
        let mut tally_deny = 0u32;
        let mut tally_abstain = 0u32;

        for v in &verdicts {
            match v.vote {
                Vote::Allow => tally_allow += 1,
                Vote::Revise => tally_revise += 1,
                Vote::Deny => tally_deny += 1,
                Vote::Abstain => tally_abstain += 1,
            }
        }

        let conflict = Self::compute_conflict(&verdicts);

        let final_action = if conflict {
            if self.break_glass.enabled {
                // Audited break-glass: deployment may map to Allow/Revise; default Allow for minimal semantics.
                Vote::Allow
            } else {
                Vote::Deny
            }
        } else {
            Self::majority_vote(tally_allow, tally_revise, tally_deny, tally_abstain)
        };

        EnsembleReport {
            verdicts,
            tally_allow,
            tally_revise,
            tally_deny,
            tally_abstain,
            conflict,
            final_action,
        }
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

    #[test]
    fn conflict_defaults_to_deny_without_break_glass() {
        let je = JudgeEnsemble::new();
        let r = je.tally(vec![v("a", Vote::Allow), v("b", Vote::Deny)]);
        assert!(r.conflict);
        assert_eq!(r.final_action, Vote::Deny);
    }

    #[test]
    fn conflict_allows_break_glass_when_audited() {
        let je = JudgeEnsemble::new().with_break_glass(BreakGlassPolicy { enabled: true });
        let r = je.tally(vec![v("a", Vote::Allow), v("b", Vote::Deny)]);
        assert!(r.conflict);
        assert_eq!(r.final_action, Vote::Allow);
    }

    #[test]
    fn unanimous_allow() {
        let je = JudgeEnsemble::new();
        let r = je.tally(vec![v("a", Vote::Allow), v("b", Vote::Allow)]);
        assert!(!r.conflict);
        assert_eq!(r.final_action, Vote::Allow);
    }
}
