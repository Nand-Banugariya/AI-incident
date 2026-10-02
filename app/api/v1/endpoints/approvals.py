"""Approval endpoints: approve/reject each of the three human gates, and read the decision history (§8)."""

import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.models.approval import Approval, ApprovalStage
from app.models.user import User
from app.schemas.approval import ApprovalApproveRequest, ApprovalRead, ApprovalRejectRequest
from app.services.approval_service import STAGE_GATE_STATE, ApprovalService
from app.services.exceptions import ConflictError, ForbiddenError, NotFoundError

router = APIRouter(prefix="/incidents", tags=["Approvals"])

_RESPONSES = {
    404: {"description": "Incident not found in the caller's organization"},
    403: {"description": "Segregation of duties: the incident's creator cannot approve a high-risk change"},
    409: {"description": "Incident is not at this stage's gate, or the stage already has this decision"},
}


def _decide(
    db: Session, incident_id: uuid.UUID, stage: ApprovalStage, approve: bool, reason: str | None, user: User
) -> Approval:
    try:
        return ApprovalService.decide(
            db=db, incident_id=incident_id, stage=stage, approve=approve, reason=reason, current_user=user
        )
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    except ForbiddenError as err:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err))
    except ConflictError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err))


def _register_decision_routes(stage: ApprovalStage) -> None:
    gate_state = STAGE_GATE_STATE[stage].value

    def approve(
        incident_id: uuid.UUID,
        current_user: Annotated[User, Depends(require_permission("approvals:write"))],
        db: Annotated[Session, Depends(get_db)],
        payload: ApprovalApproveRequest | None = None,
    ) -> Approval:
        return _decide(db, incident_id, stage, True, payload.reason if payload else None, current_user)

    def reject(
        incident_id: uuid.UUID,
        payload: ApprovalRejectRequest,
        current_user: Annotated[User, Depends(require_permission("approvals:write"))],
        db: Annotated[Session, Depends(get_db)],
    ) -> Approval:
        return _decide(db, incident_id, stage, False, payload.reason, current_user)

    approve.__name__ = f"approve_{stage.value}"
    reject.__name__ = f"reject_{stage.value}"

    router.add_api_route(
        f"/{{incident_id}}/approve-{stage.value}",
        approve,
        methods=["POST"],
        response_model=ApprovalRead,
        summary=f"Approve the {stage.value} stage",
        description=(
            f"Records an approval for the '{stage.value}' stage. Only possible while the incident is in "
            f"'{gate_state}'. Approving a stage that was rejected re-approves it (a new history row). "
            "A high-risk (or not yet risk-assessed) change cannot be approved by the user who created the "
            "incident. Requires 'approvals:write'."
        ),
        responses=_RESPONSES,
    )
    router.add_api_route(
        f"/{{incident_id}}/reject-{stage.value}",
        reject,
        methods=["POST"],
        response_model=ApprovalRead,
        summary=f"Reject the {stage.value} stage",
        description=(
            f"Records a rejection (reason required) for the '{stage.value}' stage. Only possible while the "
            f"incident is in '{gate_state}'. Rejecting an already-approved stage revokes that approval. "
            "Requires 'approvals:write'."
        ),
        responses=_RESPONSES,
    )


for _stage in ApprovalStage:
    _register_decision_routes(_stage)


@router.get(
    "/{incident_id}/approvals",
    response_model=list[ApprovalRead],
    summary="Approval history",
    description="Every approval request and decision for the incident, newest first. Requires 'incidents:read'.",
    responses={404: _RESPONSES[404]},
)
def list_approvals(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
    stage: Annotated[ApprovalStage | None, Query(description="Only this stage")] = None,
) -> list[Approval]:
    try:
        return ApprovalService.history(db=db, incident_id=incident_id, current_user=current_user, stage=stage)
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
