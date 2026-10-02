"""Data source API endpoints: registration, capabilities and connection tests."""

import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission, require_role
from app.models.data_source import DataSource, DataSourceCapability, DataSourceHealth
from app.models.user import User
from app.schemas.data_source import (
    DataSourceCapabilityRead,
    DataSourceCreate,
    DataSourceRead,
    DataSourceTestResult,
    DataSourceUpdate,
)
from app.security.rbac import Role
from app.services.data_source_service import DataSourceService
from app.services.exceptions import ConflictError, NotFoundError, NotSupportedError

router = APIRouter(prefix="/data-sources", tags=["Data Sources"])

_NOT_FOUND = {404: {"description": "Data source not found in the caller's organization"}}


@router.post(
    "",
    response_model=DataSourceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a data source",
    description=(
        "Registers an evidence source scoped to the caller's organization. configuration_json must not contain "
        "credentials; reference them via credential_reference. Requires 'data_sources:write'."
    ),
)
def create_data_source(
    payload: DataSourceCreate,
    current_user: Annotated[User, Depends(require_permission("data_sources:write"))],
    db: Annotated[Session, Depends(get_db)],
) -> DataSource:
    return DataSourceService.create(db=db, payload=payload, current_user=current_user)


@router.get(
    "",
    response_model=list[DataSourceRead],
    summary="List data sources",
    description="Lists the caller's organization's data sources. Requires 'incidents:read'.",
)
def list_data_sources(
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
    environment: Annotated[str | None, Query(max_length=50)] = None,
    provider_type: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DataSource]:
    return DataSourceService.list(
        db=db,
        current_user=current_user,
        environment=environment,
        provider_type=provider_type,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{data_source_id}",
    response_model=DataSourceRead,
    summary="Get a data source",
    description="Requires 'incidents:read'.",
    responses=_NOT_FOUND,
)
def get_data_source(
    data_source_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
) -> DataSource:
    try:
        return DataSourceService.get(db=db, data_source_id=data_source_id, current_user=current_user)
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))


@router.put(
    "/{data_source_id}",
    response_model=DataSourceRead,
    summary="Update a data source",
    description=(
        "Updates the supplied fields; omitted fields are unchanged and provider_type is immutable. "
        "A supplied 'capabilities' list replaces the full set. Requires the 'admin' role."
    ),
    responses=_NOT_FOUND,
)
def update_data_source(
    data_source_id: uuid.UUID,
    payload: DataSourceUpdate,
    current_user: Annotated[User, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> DataSource:
    try:
        return DataSourceService.update(
            db=db, data_source_id=data_source_id, payload=payload, current_user=current_user
        )
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))


@router.delete(
    "/{data_source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a data source",
    description=(
        "Permanently deletes a data source and its capabilities and health history. Refused (409) once the source "
        "has produced evidence; disable it instead. Requires the 'admin' role."
    ),
    responses={**_NOT_FOUND, 409: {"description": "The data source has collected evidence"}},
)
def delete_data_source(
    data_source_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        DataSourceService.delete(db=db, data_source_id=data_source_id, current_user=current_user)
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    except ConflictError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{data_source_id}/test",
    response_model=DataSourceTestResult,
    summary="Test a data source connection",
    description=(
        "Runs the provider's connection probe and records the result in data_source_health. "
        "Returns 501 for provider types without a probe. Requires 'data_sources:write'."
    ),
    responses={**_NOT_FOUND, 501: {"description": "No connection probe exists for this provider type yet"}},
)
def test_data_source(
    data_source_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("data_sources:write"))],
    db: Annotated[Session, Depends(get_db)],
) -> DataSourceHealth:
    try:
        return DataSourceService.test_connection(db=db, data_source_id=data_source_id, current_user=current_user)
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    except NotSupportedError as err:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(err))


@router.get(
    "/{data_source_id}/capabilities",
    response_model=list[DataSourceCapabilityRead],
    summary="List a data source's capabilities",
    description="Requires 'incidents:read'.",
    responses=_NOT_FOUND,
)
def get_data_source_capabilities(
    data_source_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[DataSourceCapability]:
    try:
        data_source = DataSourceService.get(db=db, data_source_id=data_source_id, current_user=current_user)
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    return sorted(data_source.capabilities, key=lambda c: c.capability)
