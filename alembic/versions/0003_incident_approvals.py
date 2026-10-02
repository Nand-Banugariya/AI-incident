"""incident approvals

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01 22:24:33.442670
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUMS = {
    'approvalstage': ('PLAN', 'CODE', 'PRODUCTION'),
    'approvalstatus': ('PENDING', 'APPROVED', 'REJECTED'),
}


def _enum(name: str) -> sa.Enum:
    """Portable enum column type; on PostgreSQL the type is created explicitly in upgrade()."""
    values = _ENUMS[name]
    return sa.Enum(*values, name=name).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table('incident_approvals',
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('stage', _enum('approvalstage'), nullable=False),
    sa.Column('status', _enum('approvalstatus'), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.String(length=1000), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("status != 'REJECTED' OR (reason IS NOT NULL AND reason != '')", name=op.f('ck_incident_approvals_rejected_requires_reason')),
    sa.CheckConstraint("status = 'PENDING' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)", name=op.f('ck_incident_approvals_decided_requires_actor')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_incident_approvals_decided_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_incident_approvals_incident_id_incidents'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_approvals'))
    )
    with op.batch_alter_table('incident_approvals', schema=None) as batch_op:
        batch_op.create_index('ix_incident_approvals_incident_stage_created', ['incident_id', 'stage', 'created_at'], unique=False)
        batch_op.create_index('uq_incident_approvals_one_pending_per_stage', ['incident_id', 'stage'], unique=True, postgresql_where=sa.text("status = 'PENDING'"), sqlite_where=sa.text("status = 'PENDING'"))



def downgrade() -> None:
    with op.batch_alter_table('incident_approvals', schema=None) as batch_op:
        batch_op.drop_index('uq_incident_approvals_one_pending_per_stage', postgresql_where=sa.text("status = 'PENDING'"), sqlite_where=sa.text("status = 'PENDING'"))
        batch_op.drop_index('ix_incident_approvals_incident_stage_created')

    op.drop_table('incident_approvals')

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)
