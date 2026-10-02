"""risk level: incidents.risk_level (drives segregation of duties) and a per-decision snapshot

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01 22:41:43.022155
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RISK_VALUES = ('LOW', 'MEDIUM', 'HIGH')


def _risk_enum() -> sa.Enum:
    """Shared by two tables, so on PostgreSQL the type is created once explicitly in upgrade()."""
    return sa.Enum(*_RISK_VALUES, name='risklevel').with_variant(
        postgresql.ENUM(*_RISK_VALUES, name='risklevel', create_type=False), 'postgresql'
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*_RISK_VALUES, name='risklevel').create(bind, checkfirst=True)

    # Nullable: existing incidents/approvals have no risk assessment (NULL is treated as high-risk by the rule)
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('risk_level', _risk_enum(), nullable=True))

    with op.batch_alter_table('incident_approvals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('risk_level', _risk_enum(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('incident_approvals', schema=None) as batch_op:
        batch_op.drop_column('risk_level')

    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_column('risk_level')

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*_RISK_VALUES, name='risklevel').drop(bind, checkfirst=True)
