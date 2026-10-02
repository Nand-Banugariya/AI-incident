"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-01 22:04:26.857124
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLE_VALUES = ('VIEWER', 'APPROVER', 'ADMIN')
_INCIDENTSTATE_VALUES = ('CREATED', 'PLANNING', 'COLLECTING_EVIDENCE', 'CORRELATING', 'ROOT_CAUSE_ANALYSIS', 'IMPLEMENTATION_PLAN_READY', 'WAITING_PLAN_APPROVAL', 'IMPLEMENTING', 'TESTING', 'IMPLEMENTATION_READY', 'WAITING_CODE_APPROVAL', 'PR_CREATED', 'QA', 'WAITING_PRODUCTION_APPROVAL', 'DEPLOYING', 'VERIFYING', 'RESOLVED', 'INVESTIGATION_FAILED', 'NEEDS_MORE_INVESTIGATION')
_INVESTIGATIONRUNSTATUS_VALUES = ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')

_ENUMS = {
    'role': _ROLE_VALUES,
    'incidentstate': _INCIDENTSTATE_VALUES,
    'investigationrunstatus': _INVESTIGATIONRUNSTATUS_VALUES,
}


def _enum(name: str) -> sa.Enum:
    """Enum column type. On PostgreSQL the type is created once explicitly in upgrade() (create_type=False here),
    because an enum shared by several tables (incidentstate) would otherwise be created twice."""
    values = _ENUMS[name]
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )



def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table('organizations',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations'))
    )
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_organizations_name'), ['name'], unique=True)
        batch_op.create_index(batch_op.f('ix_organizations_slug'), ['slug'], unique=True)

    op.create_table('users',
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=True),
    sa.Column('hashed_password', sa.String(length=255), nullable=False),
    sa.Column('role', _enum('role'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_users_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_org_id'), ['org_id'], unique=False)

    op.create_table('incidents',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('environment', sa.String(length=50), nullable=False),
    sa.Column('service', sa.String(length=100), nullable=True),
    sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('error_hint', sa.String(length=500), nullable=True),
    sa.Column('correlation_id', sa.String(length=100), nullable=True),
    sa.Column('state', _enum('incidentstate'), nullable=False),
    sa.Column('langgraph_thread_id', sa.String(length=100), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('total_llm_cost_usd', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_incidents_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_incidents_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incidents'))
    )
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_incidents_correlation_id'), ['correlation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_incidents_langgraph_thread_id'), ['langgraph_thread_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_incidents_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_incidents_service'), ['service'], unique=False)
        batch_op.create_index(batch_op.f('ix_incidents_state'), ['state'], unique=False)

    op.create_table('investigation_runs',
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('triggered_by', sa.Uuid(), nullable=False),
    sa.Column('status', _enum('investigationrunstatus'), nullable=False),
    sa.Column('current_node', sa.String(length=100), nullable=True),
    sa.Column('outcome_state', _enum('incidentstate'), nullable=True),
    sa.Column('error', sa.String(length=2000), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_investigation_runs_incident_id_incidents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], name=op.f('fk_investigation_runs_triggered_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_investigation_runs'))
    )
    with op.batch_alter_table('investigation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_investigation_runs_incident_id'), ['incident_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_investigation_runs_status'), ['status'], unique=False)
        batch_op.create_index('uq_investigation_runs_one_active_per_incident', ['incident_id'], unique=True, postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"), sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING')"))

    op.create_table('incident_state_transitions',
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('from_state', _enum('incidentstate'), nullable=True),
    sa.Column('to_state', _enum('incidentstate'), nullable=False),
    sa.Column('changed_by', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['changed_by'], ['users.id'], name=op.f('fk_incident_state_transitions_changed_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_incident_state_transitions_incident_id_incidents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['investigation_runs.id'], name=op.f('fk_incident_state_transitions_run_id_investigation_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_state_transitions'))
    )
    with op.batch_alter_table('incident_state_transitions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_incident_state_transitions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_incident_state_transitions_incident_id'), ['incident_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('incident_state_transitions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_incident_state_transitions_incident_id'))
        batch_op.drop_index(batch_op.f('ix_incident_state_transitions_created_at'))

    op.drop_table('incident_state_transitions')
    with op.batch_alter_table('investigation_runs', schema=None) as batch_op:
        batch_op.drop_index('uq_investigation_runs_one_active_per_incident', postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"), sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING')"))
        batch_op.drop_index(batch_op.f('ix_investigation_runs_status'))
        batch_op.drop_index(batch_op.f('ix_investigation_runs_incident_id'))

    op.drop_table('investigation_runs')
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_incidents_state'))
        batch_op.drop_index(batch_op.f('ix_incidents_service'))
        batch_op.drop_index(batch_op.f('ix_incidents_org_id'))
        batch_op.drop_index(batch_op.f('ix_incidents_langgraph_thread_id'))
        batch_op.drop_index(batch_op.f('ix_incidents_correlation_id'))

    op.drop_table('incidents')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_org_id'))
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organizations_slug'))
        batch_op.drop_index(batch_op.f('ix_organizations_name'))

    op.drop_table('organizations')

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)
