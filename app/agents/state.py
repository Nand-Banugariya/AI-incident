"""LangGraph state carried between investigation nodes (persisted by the checkpointer)."""

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    # Incident context (set once by the worker; JSON-serializable for the checkpointer)
    incident_id: str
    run_id: str
    org_id: str
    environment: str
    service: str | None
    start_time: str
    end_time: str
    error_hint: str | None
    correlation_id: str | None

    # Produced by nodes
    investigation_plan: list[dict[str, Any]]  # planning: which data sources/capabilities to query
    evidence_ids: list[str]                   # collect_evidence: incident_evidence row ids
    correlations: list[dict[str, Any]]        # correlate: cross-source links
    gaps: list[str]                           # any node: evidence that could not be gathered, and why
                                              # (plain overwrite, not a reducer: the thread is reused across runs)

    # Final outcome (root_cause)
    outcome_state: str
    outcome_reason: str
