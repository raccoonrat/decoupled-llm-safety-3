//! Evidence chain + I6 visibility commit ordering (RFC §8 invariant I6, §11.1).
//!
//! No user-visible token may leave the kernel until the corresponding audit record is
//! durably appended (or an explicit deployment exemption path — not modeled here).

use std::sync::atomic::{AtomicBool, Ordering};

/// Minimum audit fields for one token step (RFC §11.1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuditRecord {
    pub trace_id: String,
    pub step_index: u64,
    pub policy_revision: u64,
    pub dcbf_summary: String,
    pub ensemble_summary: String,
    pub projection_summary: String,
}

impl AuditRecord {
    #[must_use]
    pub fn digest_line(&self) -> String {
        format!(
            "{}|{}|{}|{}|{}|{}",
            self.trace_id,
            self.step_index,
            self.policy_revision,
            self.dcbf_summary,
            self.ensemble_summary,
            self.projection_summary
        )
    }
}

/// Append-only durable sink (TEE / disk / replicated log — interface only).
pub trait AuditSink {
    fn append_durable(&mut self, record: AuditRecord) -> Result<(), AuditError>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuditError {
    AppendFailed(String),
    /// I6: `into_user_visible` called before successful append.
    NotCommitted,
}

/// Holds user-visible payload **locked** until audit append succeeds (I6).
#[derive(Debug)]
pub struct StagedOutput<T> {
    payload: Option<T>,
    committed: AtomicBool,
}

impl<T> StagedOutput<T> {
    #[must_use]
    pub fn new(payload: T) -> Self {
        Self {
            payload: Some(payload),
            committed: AtomicBool::new(false),
        }
    }

    /// RFC §11.2: append MUST succeed before any user-visible release.
    pub fn commit_evidence_chain(
        &mut self,
        sink: &mut impl AuditSink,
        record: AuditRecord,
    ) -> Result<(), AuditError> {
        sink.append_durable(record)?;
        self.committed.store(true, Ordering::Release);
        Ok(())
    }

    /// Fail-safe: if append fails, visibility remains blocked; caller MUST deny / degrade.
    pub fn try_commit_or_fail_safe(
        &mut self,
        sink: &mut impl AuditSink,
        record: AuditRecord,
    ) -> Result<(), AuditError> {
        self.commit_evidence_chain(sink, record)
    }

    /// Consume staged output only after durable commit (I6).
    pub fn into_user_visible(self) -> Result<T, AuditError> {
        if !self.committed.load(Ordering::Acquire) {
            return Err(AuditError::NotCommitted);
        }
        self.payload.ok_or(AuditError::AppendFailed("staged payload missing".into()))
    }

    #[must_use]
    pub fn is_committed(&self) -> bool {
        self.committed.load(Ordering::Acquire)
    }
}

/// In-memory sink for tests and deterministic replay.
#[derive(Debug, Default)]
pub struct InMemoryAuditLog {
    pub entries: Vec<AuditRecord>,
}

impl AuditSink for InMemoryAuditLog {
    fn append_durable(&mut self, record: AuditRecord) -> Result<(), AuditError> {
        self.entries.push(record);
        Ok(())
    }
}

/// Simulates durable write failure (§0 fail-safe when audit required).
#[derive(Debug, Default)]
pub struct FailingAuditSink;

impl AuditSink for FailingAuditSink {
    fn append_durable(&mut self, _record: AuditRecord) -> Result<(), AuditError> {
        Err(AuditError::AppendFailed("storage unavailable".into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn i6_blocks_visible_without_commit() {
        let staged = StagedOutput::new("token-bytes".to_string());
        let err = staged.into_user_visible().unwrap_err();
        assert_eq!(err, AuditError::NotCommitted);
    }

    #[test]
    fn i6_releases_only_after_durable_append() {
        let mut sink = InMemoryAuditLog::default();
        let mut staged = StagedOutput::new("visible".to_string());
        let rec = AuditRecord {
            trace_id: "tr-1".into(),
            step_index: 0,
            policy_revision: 7,
            dcbf_summary: "dcbf:ok".into(),
            ensemble_summary: "ens:allow".into(),
            projection_summary: "proj:feasible".into(),
        };
        staged.commit_evidence_chain(&mut sink, rec.clone()).unwrap();
        assert_eq!(staged.into_user_visible().unwrap(), "visible");
        assert_eq!(sink.entries.len(), 1);
        assert_eq!(sink.entries[0], rec);
    }

    #[test]
    fn audit_failure_leaves_output_locked() {
        let mut sink = FailingAuditSink;
        let mut staged = StagedOutput::new("x".to_string());
        let rec = AuditRecord {
            trace_id: "t".into(),
            step_index: 0,
            policy_revision: 1,
            dcbf_summary: "".into(),
            ensemble_summary: "".into(),
            projection_summary: "".into(),
        };
        assert!(staged.commit_evidence_chain(&mut sink, rec).is_err());
        assert!(!staged.is_committed());
        assert!(matches!(
            staged.into_user_visible(),
            Err(AuditError::NotCommitted)
        ));
    }
}
