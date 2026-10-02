"""data sources

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01 22:05:11.025871
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_VALUES = ('HEALTHY', 'DEGRADED', 'UNREACHABLE')


def _status_enum() -> sa.Enum:
    return sa.Enum(*_STATUS_VALUES, name='datasourcehealthstatus').with_variant(
        postgresql.ENUM(*_STATUS_VALUES, name='datasourcehealthstatus', create_type=False), 'postgresql'
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*_STATUS_VALUES, name='datasourcehealthstatus').create(bind, checkfirst=True)

    op.create_table('data_sources',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('provider_type', sa.String(length=50), nullable=False),
    sa.Column('environment', sa.String(length=50), nullable=False),
    sa.Column('configuration_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('credential_reference', sa.String(length=255), nullable=True),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_data_sources_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_sources'))
    )
    with op.batch_alter_table('data_sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_sources_org_id'), ['org_id'], unique=False)

    op.create_table('data_source_capabilities',
    sa.Column('data_source_id', sa.Uuid(), nullable=False),
    sa.Column('capability', sa.String(length=100), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['data_source_id'], ['data_sources.id'], name=op.f('fk_data_source_capabilities_data_source_id_data_sources'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_source_capabilities')),
    sa.UniqueConstraint('data_source_id', 'capability', name='uq_data_source_capability')
    )
    with op.batch_alter_table('data_source_capabilities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_source_capabilities_data_source_id'), ['data_source_id'], unique=False)

    op.create_table('data_source_health',
    sa.Column('data_source_id', sa.Uuid(), nullable=False),
    sa.Column('status', _status_enum(), nullable=False),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('error_message', sa.String(length=1000), nullable=True),
    sa.Column('response_time_ms', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['data_source_id'], ['data_sources.id'], name=op.f('fk_data_source_health_data_source_id_data_sources'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_source_health'))
    )
    with op.batch_alter_table('data_source_health', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_source_health_data_source_id'), ['data_source_id'], unique=False)
        batch_op.create_index('ix_data_source_health_source_checked', ['data_source_id', 'last_checked_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('data_source_health', schema=None) as batch_op:
        batch_op.drop_index('ix_data_source_health_source_checked')
        batch_op.drop_index(batch_op.f('ix_data_source_health_data_source_id'))

    op.drop_table('data_source_health')
    with op.batch_alter_table('data_source_capabilities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_source_capabilities_data_source_id'))

    op.drop_table('data_source_capabilities')
    with op.batch_alter_table('data_sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_sources_org_id'))

    op.drop_table('data_sources')

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*_STATUS_VALUES, name='datasourcehealthstatus').drop(bind, checkfirst=True)
