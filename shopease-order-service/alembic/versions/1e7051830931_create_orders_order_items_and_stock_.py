"""create orders order_items and stock_compensations

Revision ID: 1e7051830931
Revises: 
Create Date: 2026-11-16 04:35:08.919407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e7051830931'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create orders, order_items and stock_compensations."""
    op.create_table('orders',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('order_number', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('shipping_address', sa.JSON(), nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('discount_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('shipping_charge', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('tax_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('order_status', sa.String(length=20), nullable=False),
    sa.Column('payment_status', sa.String(length=20), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("order_status IN ('PENDING','CONFIRMED','PROCESSING','SHIPPED','DELIVERED','CANCELLED')", name='ck_orders_status'),
    sa.CheckConstraint("payment_status IN ('PENDING','PAID','FAILED','REFUNDED')", name='ck_orders_payment_status'),
    sa.CheckConstraint('ABS(total_amount - (subtotal - discount_amount + tax_amount + shipping_charge)) < 0.005', name='ck_orders_total_matches'),
    sa.CheckConstraint('subtotal >= 0 AND discount_amount >= 0 AND shipping_charge >= 0 AND tax_amount >= 0 AND total_amount >= 0', name='ck_orders_amounts_non_negative'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_number', name='uq_orders_order_number'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index(op.f('ix_orders_created_at'), 'orders', ['created_at'], unique=False)
    op.create_index(op.f('ix_orders_order_status'), 'orders', ['order_status'], unique=False)
    op.create_index(op.f('ix_orders_user_id'), 'orders', ['user_id'], unique=False)
    op.create_table('stock_compensations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('order_number', sa.String(length=32), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('reserve_reference', sa.String(length=100), nullable=False),
    sa.Column('release_reference', sa.String(length=100), nullable=False),
    sa.Column('reservation_confirmed', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('PENDING','DONE')", name='ck_stock_compensations_status'),
    sa.CheckConstraint('quantity > 0', name='ck_stock_compensations_quantity'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('release_reference', name='uq_stock_compensations_release_reference'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index(op.f('ix_stock_compensations_order_number'), 'stock_compensations', ['order_number'], unique=False)
    op.create_index(op.f('ix_stock_compensations_status'), 'stock_compensations', ['status'], unique=False)
    op.create_table('order_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('product_name', sa.String(length=150), nullable=False),
    sa.Column('sku', sa.String(length=50), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('discount_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('total_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('stock_status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("stock_status IN ('RESERVED','RELEASED')", name='ck_order_items_stock_status'),
    sa.CheckConstraint('ABS(total_price - (unit_price * quantity - discount_amount)) < 0.005', name='ck_order_items_total_matches'),
    sa.CheckConstraint('quantity > 0', name='ck_order_items_quantity_positive'),
    sa.CheckConstraint('unit_price >= 0 AND discount_amount >= 0 AND total_price >= 0', name='ck_order_items_amounts_non_negative'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index(op.f('ix_order_items_order_id'), 'order_items', ['order_id'], unique=False)
    op.create_index(op.f('ix_order_items_product_id'), 'order_items', ['product_id'], unique=False)


def downgrade() -> None:
    """Drop the three tables."""
    op.drop_index(op.f('ix_order_items_product_id'), table_name='order_items')
    op.drop_index(op.f('ix_order_items_order_id'), table_name='order_items')
    op.drop_table('order_items')
    op.drop_index(op.f('ix_stock_compensations_status'), table_name='stock_compensations')
    op.drop_index(op.f('ix_stock_compensations_order_number'), table_name='stock_compensations')
    op.drop_table('stock_compensations')
    op.drop_index(op.f('ix_orders_user_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_order_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_created_at'), table_name='orders')
    op.drop_table('orders')
