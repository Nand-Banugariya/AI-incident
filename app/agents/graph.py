"""LangGraph investigation graph definition (§6)."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agents.nodes.investigation import (
    collect_evidence_node,
    correlate_node,
    planning_node,
    root_cause_node,
)
from app.agents.state import InvestigationState


def build_investigation_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    graph = StateGraph(InvestigationState)
    graph.add_node("planning", planning_node)
    graph.add_node("collect_evidence", collect_evidence_node)
    graph.add_node("correlate", correlate_node)
    graph.add_node("root_cause", root_cause_node)

    graph.add_edge(START, "planning")
    graph.add_edge("planning", "collect_evidence")
    graph.add_edge("collect_evidence", "correlate")
    graph.add_edge("correlate", "root_cause")
    graph.add_edge("root_cause", END)
    # Phase 7+: implementation_planner → plan_approval_gate (interrupt) → ...

    return graph.compile(checkpointer=checkpointer)
