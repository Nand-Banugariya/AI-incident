"""Read-side service: an incident's status snapshot and its merged audit timeline (§0 Auditability).

The timeline is rebuilt from the database alone (state transitions, approval decisions, agent tool calls),
so it stays reconstructable after logs rotate. Tool inputs are deliberately not exposed here.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_run import AgentToolCall
from app.models.approval import Approval, ApprovalStatus
from app.models.incident import Incident
from app.models.investigation import IncidentStateTransition, InvestigationRun
from app.models.user import User
from app.schemas.activity import IncidentStatusRead, TimelineEvent
from app.services.approval_service import STAGE_GATE_STATE
from app.services.evidence_service import EvidenceService
from app.services.exceptions import NotFoundError

_AWAITING_STAGE = {state: stage for stage, state in STAGE_GATE_STATE.items()}


class ActivityService:
    @staticmethod
    def _get_incident(db: Session, incident_id: uuid.UUID, current_user: User) -> Incident:
        incident = db.execute(
            select(Incident).where(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        ).scalar_one_or_none()
        if not incident:
            raise NotFoundError(f"Incident with ID '{incident_id}' not found")
        return incident

    @staticmethod
    def status(db: Session, incident_id: uuid.UUID, current_user: User) -> IncidentStatusRead:
        incident = ActivityService._get_incident(db, incident_id, current_user)
        latest_run = db.scalar(
            select(InvestigationRun)
            .where(InvestigationRun.incident_id == incident.id)
            .order_by(InvestigationRun.created_at.desc())
            .limit(1)
        )
        return IncidentStatusRead(
            incident_id=incident.id,
            state=incident.state,
            risk_level=incident.risk_level,
            awaiting_approval=_AWAITING_STAGE.get(incident.state),
            total_llm_cost_usd=incident.total_llm_cost_usd,
            evidence_count=EvidenceService.count(db, incident.id),
            latest_run=latest_run,
            updated_at=incident.updated_at,
        )

    @staticmethod
    def timeline(
        db: Session, incident_id: uuid.UUID, current_user: User, *, limit: int = 200, offset: int = 0
    ) -> list[TimelineEvent]:
        """Oldest-first merge of transitions, approvals and tool calls."""
        incident = ActivityService._get_incident(db, incident_id, current_user)
        window = offset + limit  # enough from each source to serve any page of the merged list

        events: list[TimelineEvent] = []

        for t in db.scalars(
            select(IncidentStateTransition)
            .where(IncidentStateTransition.incident_id == incident.id)
            .order_by(IncidentStateTransition.created_at, IncidentStateTransition.id)
            .limit(window)
        ):
            origin = t.from_state.value if t.from_state else "start"
            events.append(
                TimelineEvent(
                    id=f"state_transition:{t.id}",
                    timestamp=t.created_at,
                    kind="state_transition",
                    summary=f"{origin} -> {t.to_state.value}",
                    actor_id=t.changed_by,
                    details={
                        "from_state": t.from_state.value if t.from_state else None,
                        "to_state": t.to_state.value,
                        "reason": t.reason,
                        "run_id": str(t.run_id) if t.run_id else None,
                    },
                )
            )

        # Approvals are human actions, so there are few; take them all
        for a in db.scalars(select(Approval).where(Approval.incident_id == incident.id)):
            base = {"approval_id": str(a.id), "stage": a.stage.value, "risk_level": a.risk_level.value if a.risk_level else None}
            if a.status == ApprovalStatus.PENDING:
                events.append(
                    TimelineEvent(
                        id=f"approval_requested:{a.id}",
                        timestamp=a.created_at,
                        kind="approval_requested",
                        summary=f"{a.stage.value} approval requested",
                        actor_id=None,
                        details={**base, "status": a.status.value},
                    )
                )
            else:
                events.append(
                    TimelineEvent(
                        id=f"approval_decision:{a.id}",
                        timestamp=a.decided_at or a.created_at,
                        kind="approval_decision",
                        summary=f"{a.stage.value} {a.status.value}",
                        actor_id=a.decided_by,
                        details={**base, "status": a.status.value, "reason": a.reason},
                    )
                )

        for c in db.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.incident_id == incident.id)
            .order_by(AgentToolCall.started_at, AgentToolCall.id)
            .limit(window)
        ):
            events.append(
                TimelineEvent(
                    id=f"tool_call:{c.id}",
                    timestamp=c.started_at,
                    kind="tool_call",
                    summary=f"{c.mcp_server}.{c.tool_name} {'ok' if c.success else 'failed'}",
                    actor_id=None,
                    details={
                        "agent_node": c.agent_node,
                        "mcp_server": c.mcp_server,
                        "tool_name": c.tool_name,
                        "success": c.success,
                        "duration_ms": c.duration_ms,
                        "is_write_operation": c.is_write_operation,
                        "error": c.error,
                        "run_id": str(c.run_id) if c.run_id else None,
                    },
                )
            )

        events.sort(key=lambda e: (e.timestamp, e.id))
        return events[offset : offset + limit]
