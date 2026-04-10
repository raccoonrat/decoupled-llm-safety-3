//! Ring-0: polynomial-time constraint projection, algebraic composition, fail-safe degradation.

pub mod algebraic_composer;
pub mod axiom_hive_solver;
pub mod evidence_chain_audit;
pub mod execution_context;
pub mod gateway;
pub mod graceful_degradation_fsm;
pub mod graceful_degradation;
pub mod ipc_mmap_bridge;
pub mod judge_ensemble;
pub mod solver_ladder;
