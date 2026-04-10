//! Request-scoped execution handle (RFC §5.0 `ExecutionContext` minimal surface).

/// Holds sanitized input, trace id, and policy revision for the whole request / token steps.
#[derive(Debug, Clone)]
pub struct ExecutionContext {
    pub trace_id: String,
    pub policy_revision: u64,
    pub sanitized_prompt: Vec<u8>,
}

impl ExecutionContext {
    #[must_use]
    pub fn new(trace_id: String, policy_revision: u64, sanitized_prompt: Vec<u8>) -> Self {
        Self {
            trace_id,
            policy_revision,
            sanitized_prompt,
        }
    }
}
