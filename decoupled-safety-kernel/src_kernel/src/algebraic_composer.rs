//! Semilattice meet for composing safety operators (Theorem 3.3).

/// Abstract safety policy handle; meet computes intersection in the semilattice.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct PolicyId(pub u32);

/// Composes multiple policies via meet (greatest lower bound).
#[derive(Debug, Default)]
pub struct AlgebraicComposer;

impl AlgebraicComposer {
    #[must_use]
    pub fn new() -> Self {
        Self
    }
}
