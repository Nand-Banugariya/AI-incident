"""Write-capable incident actions, each behind BOTH enforcement layers (§4, §8).

    layer 1: require_permission("approvals:write")   - RBAC: is this user allowed to trigger writes at all?
    layer 2: require_approval(<stage>)               - is there an APPROVED row for exactly this incident+stage?

The real implementations land in later phases (controlled implementation, Git/Jenkins, deployment). Until then
every action returns 501 once both gates pass, so the gating is in place, and testable, before any write
capability exists. A route can only be added here with an explicit stage, which keeps "no write without an
approval row" structural rather than a convention.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_approval, require_permission
from app.models.approval import Approval, ApprovalStage
from app.models.user import User

router = APIRouter(prefix="/incidents", tags=["Gated Actions"])


@dataclass(frozen=True)
class GatedAction:
    path: str  # POST /incidents/{incident_id}/<path>
    stage: ApprovalStage  # the approval that must exist
    summary: str


GATED_ACTIONS: tuple[GatedAction, ...] = (
    GatedAction("implement", ApprovalStage.PLAN, "Apply the approved implementation plan"),
    GatedAction("create-pr", ApprovalStage.CODE, "Open a pull request for the approved change"),
    GatedAction("deploy-qa", ApprovalStage.CODE, "Deploy the approved change to QA"),
    GatedAction("deploy-production", ApprovalStage.PRODUCTION, "Deploy the approved change to production"),
)


def _register(action: GatedAction) -> None:
    def handler(
        incident_id: uuid.UUID,
        current_user: Annotated[User, Depends(require_permission("approvals:write"))],  # layer 1
        approval: Annotated[Approval, Depends(require_approval(action.stage))],  # layer 2
    ) -> None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"'{action.path}' is not implemented yet (approval gate passed)",
        )

    handler.__name__ = action.path.replace("-", "_")
    router.add_api_route(
        f"/{{incident_id}}/{action.path}",
        handler,
        methods=["POST"],
        summary=action.summary,
        description=(
            f"Requires 'approvals:write' AND an approved '{action.stage.value}' approval for this incident. "
            "Currently a stub: returns 501 once both checks pass."
        ),
        responses={
            403: {"description": f"Missing permission, or no approved '{action.stage.value}' approval"},
            404: {"description": "Incident not found in the caller's organization"},
            501: {"description": "Gate passed; the action itself is not implemented yet"},
        },
    )


for _action in GATED_ACTIONS:
    _register(_action)
