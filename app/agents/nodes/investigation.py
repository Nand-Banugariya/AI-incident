"""Investigation pipeline nodes: planning → collect_evidence → correlate → root_cause.

Phase 1 skeleton: each node performs its real bookkeeping (state transitions, gap reporting),
but source discovery (Phase 2), evidence collection via MCP (Phase 3/4) and LLM root-cause
analysis (Phase 5) are not wired yet. With no data sources, the run ends honestly in
NEEDS_MORE_INVESTIGATION with the gap recorded — never a fabricated root cause.
"""

from typing import Any

from app.agents.guards.cost import check_cost_budget
from app.agents.nodes.base import investigation_node
from app.agents.state import InvestigationState
from app.models.incident import IncidentState


def discover_data_sources(org_id: str, environment: str) -> list[dict[str, Any]]:
    """Returns enabled, healthy data sources for the org/environment.

    Phase 2: query data_sources + data_source_health, excluding 'unreachable' sources (§2).
    """
    return []


@investigation_node("planning", IncidentState.PLANNING)
def planning_node(state: InvestigationState) -> dict:
    sources = discover_data_sources(state["org_id"], state["environment"])
    if not sources:
        return {
            "investigation_plan": [],
            "gaps": [*state.get("gaps", []), f"No data sources configured for environment '{state['environment']}'"],
        }
    return {"investigation_plan": [{"source_id": s["id"], "capabilities": s["capabilities"]} for s in sources]}


@investigation_node("collect_evidence", IncidentState.COLLECTING_EVIDENCE)
def collect_evidence_node(state: InvestigationState) -> dict:
    # Phase 3: call each planned source's read-only MCP tools, persist incident_evidence rows
    # (deduplicated by content_hash, §0), and append a gap for every source that fails.
    return {"evidence_ids": []}


@investigation_node("correlate", IncidentState.CORRELATING)
def correlate_node(state: InvestigationState) -> dict:
    # Phase 5: correlate evidence by correlation_id / timestamp window / service.
    return {"correlations": []}


@investigation_node("root_cause", IncidentState.ROOT_CAUSE_ANALYSIS)
def root_cause_node(state: InvestigationState) -> dict:
    check_cost_budget(state["incident_id"])

    if not state.get("evidence_ids"):
        gaps = state.get("gaps") or []
        return {
            "outcome_state": IncidentState.NEEDS_MORE_INVESTIGATION.value,
            "outcome_reason": "No evidence collected: " + ("; ".join(gaps) if gaps else "all sources returned nothing"),
        }

    # Phase 5: structured RootCauseAnalysis LLM call with validation-retry (§3).
    return {
        "outcome_state": IncidentState.NEEDS_MORE_INVESTIGATION.value,
        "outcome_reason": "Root-cause analysis is not implemented yet",
    }
