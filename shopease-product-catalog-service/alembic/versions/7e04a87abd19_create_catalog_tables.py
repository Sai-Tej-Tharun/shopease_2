"""create categories products and inventory transactions

Revision ID: 7e04a87abd19
Revises: 
Create Date: 2026-11-13 04:36:34.952660

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e04a87abd19'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create categories, products and inventory_transactions."""
    op.create_table('categories',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_categories_name'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB',
    )
    op.create_table('products',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('category_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('sku', sa.String(length=50), nullable=False),
    sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('discount_price', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('stock_quantity', sa.Integer(), server_default='0', nullable=False),
    sa.Column('image_url', sa.String(length=500), nullable=True),
    sa.Column('brand', sa.String(length=100), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('discount_price IS NULL OR (discount_price >= 0 AND discount_price < price)', name='ck_products_discount_valid'),
    sa.CheckConstraint('price > 0', name='ck_products_price_positive'),
    sa.CheckConstraint('stock_quantity >= 0', name='ck_products_stock_non_negative'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sku', name='uq_products_sku'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB',
    )
    op.create_index(op.f('ix_products_category_id'), 'products', ['category_id'], unique=False)
    op.create_index(op.f('ix_products_name'), 'products', ['name'], unique=False)
    op.create_table('inventory_transactions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('transaction_type', sa.String(length=3), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('previous_stock', sa.Integer(), nullable=False),
    sa.Column('updated_stock', sa.Integer(), nullable=False),
    sa.Column('remarks', sa.String(length=255), nullable=True),
    sa.Column('performed_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("transaction_type IN ('IN', 'OUT')", name='ck_inventory_type'),
    sa.CheckConstraint('previous_stock >= 0 AND updated_stock >= 0', name='ck_inventory_stock_non_negative'),
    sa.CheckConstraint('quantity > 0', name='ck_inventory_quantity_positive'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB',
    )
    op.create_index(op.f('ix_inventory_transactions_product_id'), 'inventory_transactions', ['product_id'], unique=False)


def downgrade() -> None:
    """Drop the tables in reverse order (children first)."""
    op.drop_index(op.f('ix_inventory_transactions_product_id'), table_name='inventory_transactions')
    op.drop_table('inventory_transactions')
    op.drop_index(op.f('ix_products_name'), table_name='products')
    op.drop_index(op.f('ix_products_category_id'), table_name='products')
    op.drop_table('products')
    op.drop_table('categories')
