//! Gateway: request-init sanitization. Critical findings → Hard Reject (RFC v0.2-r2 §0, §5.1).

/// Severity aligned with RFC §5.1 `Finding`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Info,
    Warn,
    High,
    Critical,
}

/// Single finding from lexical / boundary / policy-tag rules.
#[derive(Debug, Clone)]
pub struct Finding {
    pub rule_id: String,
    pub span: std::ops::Range<usize>,
    pub severity: Severity,
}

/// Canonicalized input plus findings and policy tags (RFC §5.1).
#[derive(Debug, Clone)]
pub struct SanitizedPrompt {
    pub canonical: Vec<u8>,
    pub findings: Vec<Finding>,
    pub policy_tags: Vec<String>,
}

/// Hard Reject: deny entry to downstream generation (no `SanitizedPrompt` for the pipeline).
#[derive(Debug, Clone)]
pub struct GatewayHardReject {
    pub findings: Vec<Finding>,
}

impl std::fmt::Display for GatewayHardReject {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "gateway hard reject: {} critical finding(s)",
            self.findings
                .iter()
                .filter(|x| x.severity == Severity::Critical)
                .count()
        )
    }
}

impl std::error::Error for GatewayHardReject {}

/// Ring-1 gateway filter: MUST run at request initialization (RFC §6.0).
#[derive(Debug, Default)]
pub struct GatewayFilter {
    /// Reserved for compiled rules / automata handles.
    _private: (),
}

impl GatewayFilter {
    #[must_use]
    pub fn new() -> Self {
        Self { _private: () }
    }

    /// Request-init sanitization. If any `Finding.severity == Critical`, returns **`Err(GatewayHardReject)`**
    /// — default action is **Hard Reject** (no further generation on this path) per RFC §0 / §5.1.
    pub fn sanitize_input(&self, raw_input: &[u8]) -> Result<SanitizedPrompt, GatewayHardReject> {
        let findings = self.scan_findings(raw_input);

        if findings
            .iter()
            .any(|f| f.severity == Severity::Critical)
        {
            return Err(GatewayHardReject { findings });
        }

        Ok(SanitizedPrompt {
            canonical: raw_input.to_vec(),
            findings,
            policy_tags: Vec::new(),
        })
    }

    /// Pluggable scan hook: replace with real lexical / Unicode / injection rules.
    fn scan_findings(&self, raw_input: &[u8]) -> Vec<Finding> {
        let mut out = Vec::new();
        // Example rule: empty input → High (non-blocking); demonstrative only.
        if raw_input.is_empty() {
            out.push(Finding {
                rule_id: "gateway.empty_input".to_string(),
                span: 0..0,
                severity: Severity::High,
            });
        }
        // Example Critical path for tests / integration: magic byte sequence triggers Hard Reject.
        if raw_contains(raw_input, b"__CRITICAL_TEST__") {
            if let Some(i) = find_subslice(raw_input, b"__CRITICAL_TEST__") {
                let end = i + b"__CRITICAL_TEST__".len();
                out.push(Finding {
                    rule_id: "gateway.critical_marker".to_string(),
                    span: i..end,
                    severity: Severity::Critical,
                });
            }
        }
        out
    }
}

fn raw_contains(haystack: &[u8], needle: &[u8]) -> bool {
    find_subslice(haystack, needle).is_some()
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    haystack
        .windows(needle.len())
        .position(|w| w == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn critical_triggers_hard_reject() {
        let g = GatewayFilter::new();
        let raw = b"hello __CRITICAL_TEST__ world";
        let err = g.sanitize_input(raw).unwrap_err();
        assert!(err.findings.iter().any(|f| f.severity == Severity::Critical));
    }

    #[test]
    fn non_critical_succeeds() {
        let g = GatewayFilter::new();
        let sp = g.sanitize_input(b"safe user text").unwrap();
        assert!(!sp
            .findings
            .iter()
            .any(|f| f.severity == Severity::Critical));
    }
}
