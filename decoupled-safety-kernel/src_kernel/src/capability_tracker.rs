//! Ring-0 capability accumulation and conjunctive dependency checking.
//!
//! Provides runtime evidence for Theorem 3.1 (non-compositionality of implicit
//! safety under conjunctive capability dependencies).
//!
//! In the hypergraph model, each session accumulates *capabilities* via tool
//! calls and information disclosure. A `ConjunctiveDependency` defines a rule:
//! if a session has acquired all capabilities in `requires`, and the LLM
//! produces capability `produces`, then `forbidden` capabilities become
//! reachable — constituting a safety violation.
//!
//! This module runs inside the safety kernel (Ring-0) and is consulted by the
//! Judge Ensemble and Gateway before releasing capabilities to Ring-3.

use std::collections::{HashMap, HashSet};

/// Opaque capability identifier (e.g. "system_prompt_fragment", "tool_call_web_search").
pub type CapabilityId = String;

/// Tracks capabilities accumulated during a session.
///
/// When the Gateway or Judge releases information to Ring-3, the corresponding
/// capability ID is inserted here. The accumulator is scoped per-session and
/// reset on session termination.
#[derive(Debug, Clone, Default)]
pub struct CapabilityAccumulator {
    acquired: HashSet<CapabilityId>,
    generation: u64,
}

impl CapabilityAccumulator {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn acquire(&mut self, cap: CapabilityId) {
        self.acquired.insert(cap);
        self.generation += 1;
    }

    pub fn has(&self, cap: &str) -> bool {
        self.acquired.contains(cap)
    }

    pub fn has_all(&self, caps: &HashSet<CapabilityId>) -> bool {
        caps.is_subset(&self.acquired)
    }

    pub fn acquired_set(&self) -> &HashSet<CapabilityId> {
        &self.acquired
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn reset(&mut self) {
        self.acquired.clear();
        self.generation += 1;
    }
}

/// A single conjunctive dependency rule in the capability hypergraph.
///
/// Semantics (Theorem 3.1): if the session has acquired **all** capabilities in
/// `requires` AND the model is about to produce/release capability `produces`,
/// then the capabilities in `forbidden` become reachable — which constitutes a
/// safety violation (non-compositionality).
#[derive(Debug, Clone)]
pub struct ConjunctiveDependency {
    pub rule_id: String,
    pub requires: HashSet<CapabilityId>,
    pub produces: CapabilityId,
    pub forbidden: HashSet<CapabilityId>,
}

/// Result of a conjunctive violation check.
#[derive(Debug, Clone)]
pub struct ViolationReport {
    pub violated: bool,
    pub triggering_rules: Vec<String>,
    pub reachable_forbidden: HashSet<CapabilityId>,
}

/// Checks whether acquiring a new capability would violate any conjunctive
/// dependency rule, given the current accumulator state.
///
/// This implements the runtime version of Theorem 3.1's hypergraph closure:
/// we compute the transitive closure of capabilities that become reachable
/// if `new_cap` is acquired, then check if any forbidden set is hit.
pub fn check_conjunctive_violation(
    accumulator: &CapabilityAccumulator,
    rules: &[ConjunctiveDependency],
    new_cap: &str,
) -> ViolationReport {
    let mut simulated = accumulator.acquired_set().clone();
    simulated.insert(new_cap.to_string());

    let mut changed = true;
    while changed {
        changed = false;
        for rule in rules {
            if simulated.is_superset(&rule.requires) && !simulated.contains(&rule.produces) {
                simulated.insert(rule.produces.clone());
                changed = true;
            }
        }
    }

    let mut triggering_rules = Vec::new();
    let mut reachable_forbidden = HashSet::new();
    for rule in rules {
        if simulated.is_superset(&rule.requires) && simulated.contains(&rule.produces) {
            let hit: HashSet<_> = rule.forbidden.intersection(&simulated).cloned().collect();
            if !hit.is_empty() {
                triggering_rules.push(rule.rule_id.clone());
                reachable_forbidden.extend(hit);
            }
        }
    }

    ViolationReport {
        violated: !reachable_forbidden.is_empty(),
        triggering_rules,
        reachable_forbidden,
    }
}

/// Index of conjunctive rules for efficient lookup by capability.
#[derive(Debug, Default)]
pub struct ConjunctiveRuleIndex {
    rules: Vec<ConjunctiveDependency>,
    by_produces: HashMap<CapabilityId, Vec<usize>>,
}

impl ConjunctiveRuleIndex {
    pub fn new(rules: Vec<ConjunctiveDependency>) -> Self {
        let mut by_produces: HashMap<CapabilityId, Vec<usize>> = HashMap::new();
        for (i, r) in rules.iter().enumerate() {
            by_produces.entry(r.produces.clone()).or_default().push(i);
        }
        Self { rules, by_produces }
    }

    pub fn check(&self, accumulator: &CapabilityAccumulator, new_cap: &str) -> ViolationReport {
        check_conjunctive_violation(accumulator, &self.rules, new_cap)
    }

    pub fn rules(&self) -> &[ConjunctiveDependency] {
        &self.rules
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(id: &str, requires: &[&str], produces: &str, forbidden: &[&str]) -> ConjunctiveDependency {
        ConjunctiveDependency {
            rule_id: id.to_string(),
            requires: requires.iter().map(|s| s.to_string()).collect(),
            produces: produces.to_string(),
            forbidden: forbidden.iter().map(|s| s.to_string()).collect(),
        }
    }

    #[test]
    fn no_rules_no_violation() {
        let acc = CapabilityAccumulator::new();
        let report = check_conjunctive_violation(&acc, &[], "any_cap");
        assert!(!report.violated);
    }

    #[test]
    fn single_rule_not_triggered_without_prerequisites() {
        let acc = CapabilityAccumulator::new();
        let rules = vec![rule("r1", &["cap_a", "cap_b"], "cap_c", &["cap_c"])];
        let report = check_conjunctive_violation(&acc, &rules, "cap_a");
        assert!(!report.violated);
    }

    #[test]
    fn single_rule_triggered_when_prerequisites_met() {
        let mut acc = CapabilityAccumulator::new();
        acc.acquire("cap_a".to_string());
        let rules = vec![rule("r1", &["cap_a", "cap_b"], "cap_c", &["cap_c"])];
        let report = check_conjunctive_violation(&acc, &rules, "cap_b");
        assert!(report.violated);
        assert!(report.triggering_rules.contains(&"r1".to_string()));
        assert!(report.reachable_forbidden.contains("cap_c"));
    }

    #[test]
    fn transitive_closure_triggers_violation() {
        let mut acc = CapabilityAccumulator::new();
        acc.acquire("cap_a".to_string());
        let rules = vec![
            rule("r1", &["cap_a", "cap_b"], "cap_c", &[]),
            rule("r2", &["cap_c"], "cap_d", &["cap_d"]),
        ];
        let report = check_conjunctive_violation(&acc, &rules, "cap_b");
        assert!(report.violated);
        assert!(report.triggering_rules.contains(&"r2".to_string()));
    }

    #[test]
    fn index_check_works() {
        let mut acc = CapabilityAccumulator::new();
        acc.acquire("cap_a".to_string());
        let index = ConjunctiveRuleIndex::new(vec![
            rule("r1", &["cap_a", "cap_b"], "cap_c", &["cap_c"]),
        ]);
        let report = index.check(&acc, "cap_b");
        assert!(report.violated);
    }

    #[test]
    fn accumulator_reset_clears_state() {
        let mut acc = CapabilityAccumulator::new();
        acc.acquire("cap_a".to_string());
        assert!(acc.has("cap_a"));
        acc.reset();
        assert!(!acc.has("cap_a"));
    }
}
