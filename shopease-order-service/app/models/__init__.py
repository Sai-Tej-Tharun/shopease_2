"""Importing the package registers every model on Base.metadata (Alembic and create_all rely on this)."""
from app.models.order_item_model import OrderItem  # noqa: F401
from app.models.order_model import Order  # noqa: F401
from app.models.stock_compensation_model import StockCompensation  # noqa: F401
