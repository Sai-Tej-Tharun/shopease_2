"""Pricing rules (Decimal), configuration rules and the Alembic migration."""
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.order_service import Line, build_line, calculate_totals, money
from app.services.product_client import ProductInfo
from app.services.errors import UnprocessableError

ROOT = Path(__file__).resolve().parent.parent
GOOD = dict(jwt_secret_key="s" * 40, database_url="sqlite:///:memory:")


def product(price, discount=None, stock=10, pid=1):
    return ProductInfo(pid, "P", "S", Decimal(price), None if discount is None else Decimal(discount), stock, True)


def totals(*pairs):
    return calculate_totals([build_line(product(p, d), q) for p, d, q in pairs])


def test_calculation_rules():
    t = totals(("100.00", None, 1))  # 100 < 1000 -> shipping 50; tax 18
    assert t == {"subtotal": Decimal("100.00"), "discount_amount": Decimal("0.00"), "tax_amount": Decimal("18.00"),
                 "shipping_charge": Decimal("50.00"), "total_amount": Decimal("168.00")}
    t = totals(("1500.00", "1200.00", 2), ("10.00", None, 3))  # subtotal 3030, discount 600, taxable 2430 -> tax 437.40, free shipping
    assert (t["subtotal"], t["discount_amount"], t["tax_amount"], t["shipping_charge"], t["total_amount"]) == \
           (Decimal("3030.00"), Decimal("600.00"), Decimal("437.40"), Decimal("0.00"), Decimal("2867.40"))
    assert t["total_amount"] == t["subtotal"] - t["discount_amount"] + t["tax_amount"] + t["shipping_charge"]


def test_item_amounts():
    line = build_line(product("20000.00", "18000.00"), 3)
    assert (line.unit_price, line.discount, line.total) == (Decimal("20000.00"), Decimal("6000.00"), Decimal("54000.00"))
    line = build_line(product("0.10"), 3)
    assert line.total == Decimal("0.30") and isinstance(line.total, Decimal)  # no float error (0.1 x 3)


def test_rounding_is_half_up_and_decimal_only():
    assert money(Decimal("0.005")) == Decimal("0.01") and money(Decimal("2.675")) == Decimal("2.68") and money("1.994") == Decimal("1.99")
    assert totals(("33.33", None, 3))["tax_amount"] == Decimal("18.00")  # 99.99 x 0.18 = 17.9982
    for value in totals(("10.00", None, 1)).values():
        assert isinstance(value, Decimal)


def test_invalid_calculations_are_rejected():
    bad = Line(product("10.00"), 1, Decimal("10.00"), Decimal("20.00"), Decimal("-10.00"))  # discount larger than the price
    with pytest.raises(UnprocessableError):
        calculate_totals([bad])
    mismatch = Line(product("10.00"), 1, Decimal("10.00"), Decimal("0.00"), Decimal("9.00"))  # item total does not add up
    with pytest.raises(UnprocessableError):
        calculate_totals([mismatch])
    with pytest.raises(UnprocessableError):
        totals(("9999999999.00", None, 2))  # total too large


def test_pricing_rules_are_configurable(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "tax_rate", Decimal("0.05"))
    monkeypatch.setattr(settings, "shipping_flat_rate", Decimal("99.00"))
    monkeypatch.setattr(settings, "free_shipping_threshold", Decimal("500.00"))
    assert totals(("100.00", None, 1)) == {"subtotal": Decimal("100.00"), "discount_amount": Decimal("0.00"), "tax_amount": Decimal("5.00"),
                                           "shipping_charge": Decimal("99.00"), "total_amount": Decimal("204.00")}
    assert totals(("500.00", None, 1))["shipping_charge"] == Decimal("0.00")


def make(**kw):
    return Settings(_env_file=None, **{**GOOD, **kw})


def test_secret_required_and_validated(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite:///:memory:")
    for secret in ("short", "x" * 31):
        with pytest.raises(ValidationError):
            make(jwt_secret_key=secret)
    for alg in ("none", "RS256"):
        with pytest.raises(ValidationError):
            make(jwt_algorithm=alg)


def test_database_settings_and_mysql_url(monkeypatch):
    for name in ("DATABASE_URL", "MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, jwt_secret_key="s" * 40)
    assert "MYSQL_PASSWORD" in str(e.value)
    s = Settings(_env_file=None, jwt_secret_key="s" * 40, mysql_host="order-db", mysql_database="shopease_order_db", mysql_user="shopease_order", mysql_password="p@ss:w/rd")
    assert s.sqlalchemy_url == "mysql+pymysql://shopease_order:p%40ss%3Aw%2Frd@order-db:3306/shopease_order_db"
    assert "p@ss" not in repr(s) and "s" * 40 not in repr(s)


def test_other_service_urls_and_rules_are_configurable():
    s = make(user_service_url="http://user-service:8002/", product_service_url="http://host.docker.internal:8003/", tax_rate="0.12", max_items_per_order=5)
    assert s.user_service_url == "http://user-service:8002" and s.product_service_url == "http://host.docker.internal:8003"
    assert s.tax_rate == Decimal("0.12") and s.max_items_per_order == 5
    for bad in (dict(tax_rate="-0.1"), dict(tax_rate="1.5"), dict(shipping_flat_rate="-1"), dict(order_number_prefix="bad prefix")):
        with pytest.raises(ValidationError):
            make(**bad)


def test_no_hardcoded_secrets_or_direct_database_access_to_other_services():
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text().lower()
        assert "mysql+pymysql://root" not in text and "secret_key=\"" not in text.replace(" ", ""), path
        assert "shopease_product_catalog_db" not in text and "shopease_user_db" not in text and "shopease_auth_db" not in text, path
    assert "JWT_SECRET_KEY=" not in (ROOT / "docker-compose.yml").read_text().replace("${JWT_SECRET_KEY", "")


def run_alembic(db_path, *args):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_migration_upgrade_check_downgrade():
    import sqlite3
    db = os.path.join(tempfile.mkdtemp(), "mig.db")
    r = run_alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr
    tables = {row[0] for row in sqlite3.connect(db).execute("select name from sqlite_master where type='table'")}
    assert {"orders", "order_items", "stock_compensations", "alembic_version"} <= tables
    cols = {row[1] for row in sqlite3.connect(db).execute("pragma table_info(orders)")}
    assert {"id", "order_number", "user_id", "shipping_address", "subtotal", "discount_amount", "shipping_charge", "tax_amount", "total_amount",
            "order_status", "payment_status", "notes", "created_at", "updated_at"} <= cols
    cols = {row[1] for row in sqlite3.connect(db).execute("pragma table_info(order_items)")}
    assert {"id", "order_id", "product_id", "product_name", "sku", "unit_price", "quantity", "discount_amount", "total_price", "created_at"} <= cols
    r = run_alembic(db, "check")
    assert r.returncode == 0 and "No new upgrade operations" in (r.stdout + r.stderr), r.stdout + r.stderr
    r = run_alembic(db, "downgrade", "base")
    assert r.returncode == 0, r.stderr
    left = {row[0] for row in sqlite3.connect(db).execute("select name from sqlite_master where type='table'")}
    assert not ({"orders", "order_items", "stock_compensations"} & left)


def test_docker_files_follow_the_requirements():
    import yaml
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    svc, db = compose["services"]["order-service"], compose["services"]["order-db"]
    assert "healthcheck" in db and svc["depends_on"]["order-db"]["condition"] == "service_healthy"
    assert svc["environment"]["MYSQL_HOST"] == "order-db" and "order_db_data" in compose["volumes"]
    assert {"USER_SERVICE_URL", "PRODUCT_SERVICE_URL", "AUTH_SERVICE_URL", "JWT_SECRET_KEY"} <= set(svc["environment"])
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "python:3.11" in dockerfile and "alembic upgrade head" in dockerfile and "USER" in dockerfile
