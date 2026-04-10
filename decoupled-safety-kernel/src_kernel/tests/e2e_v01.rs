//! Safety Kernel v0.1 end-to-end integration tests (决议 2).
//!
//! Pipeline: Gateway (request init) → Judge ensemble (per candidate) → Axiom Hive projection
//! → `StagedOutput` + durable audit append → user-visible release (I6).
//!
//! Ring-3 LLM is stubbed here; production uses `llm_user_space/next_token_adapter` + `scripts/e2e_full_chain.py`
//! feeding `e2e_ring3_json` (see repo `decoupled-safety-kernel/scripts/e2e_full_chain.py`).

use std::time::Instant;

use decoupled_safety_kernel::axiom_hive_solver::{
    AxiomHiveSolver, CandidateVerdict, DCBFReport, DeterministicAutomaton, ProjectionInput,
    HARD_LATENCY_BUDGET,
};
use decoupled_safety_kernel::evidence_chain_audit::{AuditRecord, InMemoryAuditLog, StagedOutput};
use decoupled_safety_kernel::gateway::{GatewayFilter, Severity};
use decoupled_safety_kernel::graceful_degradation_fsm::{DegradeAction, GracefulDegradationFsm, PageFaultReason};
use decoupled_safety_kernel::judge_ensemble::{EnsembleReport, JudgeEnsemble, Verdict, Vote};

fn allow_ensemble(je: &JudgeEnsemble) -> EnsembleReport {
    je.tally(vec![Verdict {
        vote: Vote::Allow,
        confidence: 1.0,
        explanation: String::new(),
        verifier_id: "e2e-v1".into(),
    }])
}

#[test]
fn e2e_v01_happy_path_gateway_projection_audit_release() {
    // 1) Gateway: request initialization sanitize (RFC §6.0)
    let gw = GatewayFilter::new();
    let sanitized = gw
        .sanitize_input(b"e2e user request")
        .expect("sanitized");
    assert!(!sanitized
        .findings
        .iter()
        .any(|f| f.severity == Severity::Critical));

    // 2) Stub Ring-3: vocabulary logits + top-k (replace with untrusted LLM top-k in production)
    let mut logits = vec![0.0f32; 8];
    logits[1] = 2.0;
    logits[2] = 1.5;
    logits[3] = 0.5;
    let topk_indices = [1usize, 2, 3];

    let je = JudgeEnsemble::new();
    let ens = allow_ensemble(&je);
    let candidate_verdicts = vec![
        CandidateVerdict {
            index: 1,
            ensemble: ens.clone(),
        },
        CandidateVerdict {
            index: 2,
            ensemble: ens.clone(),
        },
        CandidateVerdict {
            index: 3,
            ensemble: ens.clone(),
        },
    ];

    let dcbf = DCBFReport {
        h_t: 1.0,
        margin: 0.2,
        interrupt: false,
    };

    let automata = DeterministicAutomaton::default();
    let input = ProjectionInput {
        logits: &logits,
        topk_indices: &topk_indices,
        candidate_verdicts: &candidate_verdicts,
        automata: &automata,
        dcbf: &dcbf,
        deadline: Instant::now() + HARD_LATENCY_BUDGET,
    };

    let solver = AxiomHiveSolver::default();
    let hive = solver.enforce_projection_with_timers(input);
    assert!(
        hive.output.feasible,
        "projection should succeed on stub logits"
    );
    let chosen = hive
        .output
        .chosen_index
        .expect("chosen index");
    assert_eq!(chosen, 1, "highest logit wins argmin(-logit)");

    // 3) I6: stage bytes until audit durable append
    let token = format!("token_id={chosen}");
    let mut staged = StagedOutput::new(token.clone());
    assert!(!staged.is_committed(), "I6: no visibility before durable audit");

    let mut audit = InMemoryAuditLog::default();
    let record = AuditRecord {
        trace_id: "e2e-trace-v01".into(),
        step_index: 0,
        policy_revision: 1,
        dcbf_summary: format!("margin={}", dcbf.margin),
        ensemble_summary: "tally_allow=1".into(),
        projection_summary: format!(
            "feasible={} chosen={:?}",
            hive.output.feasible, hive.output.chosen_index
        ),
    };
    staged
        .commit_evidence_chain(&mut audit, record.clone())
        .expect("durable append");

    let visible = staged.into_user_visible().expect("I6 release");
    assert_eq!(visible, token);
    assert_eq!(audit.entries.len(), 1);
    assert_eq!(audit.entries[0].trace_id, "e2e-trace-v01");

    // Sanity: sanitized prompt bytes available for downstream (ExecutionContext in full kernel)
    assert!(!sanitized.canonical.is_empty());
}

#[test]
fn e2e_v01_empty_candidates_routes_to_degrade() {
    let fsm = GracefulDegradationFsm::new();
    let reason = PageFaultReason::EmptySafeCandidateSet;
    let action = fsm.route(&reason);
    assert_eq!(action, DegradeAction::EmitSafeTemplate);
}
