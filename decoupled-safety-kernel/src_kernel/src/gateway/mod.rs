//! Gateway (Ring-1): canonicalization, lexical/boundary rules, Critical → Hard Reject.

mod gateway_filter;

pub use gateway_filter::{
    Finding, GatewayFilter, GatewayHardReject, SanitizedPrompt, Severity,
};
