"""Service layer recording human approval decisions (§4, §8).

Approval rows are the audit trail and the only thing `require_approval()` trusts, so decisions are
append-only: a decision is a new row (or the answer to an open PENDING request), and a row that has
been decided is never edited afterwards.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import Approval, ApprovalStage, ApprovalStatus
from app.models.incident import Incident, IncidentState, RiskLevel
from app.models.user import User
from app.services.exceptions import ConflictError, ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)

# A stage can only be decided while the incident is parked at its gate
STAGE_GATE_STATE: dict[ApprovalStage, IncidentState] = {
    ApprovalStage.PLAN: IncidentState.WAITING_PLAN_APPROVAL,
    ApprovalStage.CODE: IncidentState.WAITING_CODE_APPROVAL,
    ApprovalStage.PRODUCTION: IncidentState.WAITING_PRODUCTION_APPROVAL,
}


def requires_independent_approver(risk_level: RiskLevel | None) -> bool:
    """Segregation of duties (§4): high-risk changes need an approver other than the incident's creator.

    An unassessed (NULL) risk counts as high: the rule fails closed rather than open.
    """
    return risk_level is None or risk_level == RiskLevel.HIGH


class ApprovalService:
    @staticmethod
    def _get_incident(db: Session, incident_id: uuid.UUID, current_user: User, *, lock: bool = False) -> Incident:
        query = select(Incident).where(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        if lock:
            query = query.with_for_update()  # serialises concurrent decisions on one incident (no-op on SQLite)
        incident = db.execute(query).scalar_one_or_none()
        if not incident:
            raise NotFoundError(f"Incident with ID '{incident_id}' not found")
        return incident

    @staticmethod
    def decide(
        db: Session,
        incident_id: uuid.UUID,
        stage: ApprovalStage,
        *,
        approve: bool,
        reason: str | None,
        current_user: User,
    ) -> Approval:
        """Records an approval or rejection for one stage.

        - Only valid while the incident is at that stage's gate (409 otherwise).
        - Repeating the current decision is a 409; the opposite decision is allowed, so a rejected stage can be
          re-approved and an approval can be revoked until the gated action actually starts.
        - Approving a high-risk (or unassessed-risk) change is refused when the approver created the incident (403).
          Rejecting is never restricted: refusing your own change is always safe.
        - Does not change the incident's state: the gated endpoint or the resumed graph does that.
        """
        incident = ApprovalService._get_incident(db, incident_id, current_user, lock=True)

        required_state = STAGE_GATE_STATE[stage]
        if incident.state != required_state:
            raise ConflictError(
                f"Incident is in state '{incident.state.value}'; a {stage.value} decision is only possible "
                f"in state '{required_state.value}'"
            )

        if approve and incident.created_by == current_user.id and requires_independent_approver(incident.risk_level):
            risk = incident.risk_level.value if incident.risk_level else "unassessed"
            logger.warning(
                "Self-approval blocked: incident=%s stage=%s risk=%s user=%s",
                incident.id, stage.value, risk, current_user.id,
            )
            raise ForbiddenError(
                f"A {risk}-risk change cannot be approved by the user who created the incident; "
                "another approver must decide"
            )

        target = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        latest = db.scalar(
            select(Approval)
            .where(Approval.incident_id == incident.id, Approval.stage == stage)
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        if latest is not None and latest.status == target:
            raise ConflictError(f"The {stage.value} stage is already {target.value}")

        now = datetime.now(timezone.utc)
        if latest is not None and latest.status == ApprovalStatus.PENDING:
            approval = latest  # answering the open request
        else:
            approval = Approval(incident_id=incident.id, stage=stage)
            db.add(approval)
        approval.status = target
        approval.decided_by = current_user.id
        approval.decided_at = now
        approval.reason = reason
        approval.risk_level = incident.risk_level

        db.commit()
        db.refresh(approval)

        revoked = latest is not None and latest.status == ApprovalStatus.APPROVED and not approve
        logger.log(
            logging.WARNING if revoked else logging.INFO,
            "Approval %s: incident=%s stage=%s decision=%s by user=%s%s",
            approval.id, incident.id, stage.value, target.value, current_user.id,
            " (revoked a previous approval)" if revoked else "",
        )
        return approval

    @staticmethod
    def history(
        db: Session,
        incident_id: uuid.UUID,
        current_user: User,
        *,
        stage: ApprovalStage | None = None,
    ) -> list[Approval]:
        """Full approval history for an incident, newest first."""
        incident = ApprovalService._get_incident(db, incident_id, current_user)
        query = select(Approval).where(Approval.incident_id == incident.id).order_by(Approval.created_at.desc())
        if stage is not None:
            query = query.where(Approval.stage == stage)
        return list(db.execute(query).scalars().all())
