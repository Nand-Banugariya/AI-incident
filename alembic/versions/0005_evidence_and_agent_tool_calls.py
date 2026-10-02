"""evidence and agent tool calls

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01 23:08:37.872860
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVIDENCE_TYPES = (
    'APPLICATION_LOG', 'DATABASE_LOG', 'STACK_TRACE', 'QUERY', 'KUBERNETES_EVENT', 'SERVER_EVENT',
    'DEPLOYMENT', 'GIT_COMMIT', 'SOURCE_CODE', 'METRIC', 'API_RESPONSE',
)


def _evidence_type_enum() -> sa.Enum:
    """Portable enum column type; on PostgreSQL the type is created explicitly in upgrade()."""
    return sa.Enum(*_EVIDENCE_TYPES, name='evidencetype').with_variant(
        postgresql.ENUM(*_EVIDENCE_TYPES, name='evidencetype', create_type=False), 'postgresql'
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*_EVIDENCE_TYPES, name='evidencetype').create(bind, checkfirst=True)

    op.create_table('incident_evidence',
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('evidence_type', _evidence_type_enum(), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('service', sa.String(length=100), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('normalized_content', sa.Text(), nullable=True),
    sa.Column('correlation_id', sa.String(length=100), nullable=True),
    sa.Column('severity', sa.String(length=50), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_incident_evidence_incident_id_incidents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], name=op.f('fk_incident_evidence_source_id_data_sources'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_evidence')),
    sa.UniqueConstraint('incident_id', 'content_hash', name='uq_incident_evidence_hash')
    )
    with op.batch_alter_table('incident_evidence', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_incident_evidence_correlation_id'), ['correlation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_incident_evidence_evidence_type'), ['evidence_type'], unique=False)
        batch_op.create_index('ix_incident_evidence_incident_timestamp', ['incident_id', 'timestamp'], unique=False)

    op.create_table('agent_tool_calls',
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('agent_node', sa.String(length=100), nullable=False),
    sa.Column('mcp_server', sa.String(length=100), nullable=False),
    sa.Column('tool_name', sa.String(length=100), nullable=False),
    sa.Column('input_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('output_summary', sa.String(length=2000), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('error', sa.String(length=2000), nullable=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('completion_tokens', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=10, scale=6), nullable=True),
    sa.Column('is_write_operation', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_agent_tool_calls_incident_id_incidents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['investigation_runs.id'], name=op.f('fk_agent_tool_calls_run_id_investigation_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_tool_calls'))
    )
    with op.batch_alter_table('agent_tool_calls', schema=None) as batch_op:
        batch_op.create_index('ix_agent_tool_calls_incident_started', ['incident_id', 'started_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_tool_calls_run_id'), ['run_id'], unique=False)
        batch_op.create_index('ix_agent_tool_calls_write_ops', ['started_at'], unique=False, postgresql_where=sa.text('is_write_operation = true'), sqlite_where=sa.text('is_write_operation = true'))



def downgrade() -> None:
    with op.batch_alter_table('agent_tool_calls', schema=None) as batch_op:
        batch_op.drop_index('ix_agent_tool_calls_write_ops', postgresql_where=sa.text('is_write_operation = true'), sqlite_where=sa.text('is_write_operation = true'))
        batch_op.drop_index(batch_op.f('ix_agent_tool_calls_run_id'))
        batch_op.drop_index('ix_agent_tool_calls_incident_started')

    op.drop_table('agent_tool_calls')
    with op.batch_alter_table('incident_evidence', schema=None) as batch_op:
        batch_op.drop_index('ix_incident_evidence_incident_timestamp')
        batch_op.drop_index(batch_op.f('ix_incident_evidence_evidence_type'))
        batch_op.drop_index(batch_op.f('ix_incident_evidence_correlation_id'))

    op.drop_table('incident_evidence')

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*_EVIDENCE_TYPES, name='evidencetype').drop(bind, checkfirst=True)
