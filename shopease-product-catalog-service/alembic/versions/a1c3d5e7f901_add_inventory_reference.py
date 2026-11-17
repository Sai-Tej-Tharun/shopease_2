"""add unique operation reference to inventory transactions

Revision ID: a1c3d5e7f901
Revises: 7e04a87abd19
Create Date: 2026-11-16 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c3d5e7f901'
down_revision: Union[str, Sequence[str], None] = '7e04a87abd19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotency key for stock operations requested by other services (NULL for manual changes)."""
    with op.batch_alter_table('inventory_transactions') as batch:
        batch.add_column(sa.Column('reference', sa.String(length=100), nullable=True))
        batch.create_unique_constraint('uq_inventory_reference', ['reference'])


def downgrade() -> None:
    with op.batch_alter_table('inventory_transactions') as batch:
        batch.drop_constraint('uq_inventory_reference', type_='unique')
        batch.drop_column('reference')
