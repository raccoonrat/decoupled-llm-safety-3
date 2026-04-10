// Thrift mirrors for the same cross-layer contracts (optional codegen path).

namespace py dsk.v1
namespace rs dsk.v1

struct LatentState {
  1: required string trace_id,
  2: required i32 layer_tier,
  3: required i32 schema_version,
  4: optional binary payload,
}

struct ProjectionOutput {
  1: required string trace_id,
  2: required bool accepted,
  3: required double energy,
  4: required double distance,
}

struct BottomAbsorption {
  1: required string reason,
  2: required string source_component,
}

struct DcbfBoundaryEvent {
  1: required double h_value,
  2: required double bound,
  3: required bool violated,
}

union AuditEvent {
  1: BottomAbsorption bottom,
  2: DcbfBoundaryEvent dcbf,
}

struct AuditLogEntry {
  1: required string trace_id,
  2: required i64 timestamp_unix_nanos,
  3: required AuditEvent event,
}

struct AuditLog {
  1: required list<AuditLogEntry> entries,
}
