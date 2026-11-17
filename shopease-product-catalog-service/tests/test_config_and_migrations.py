"""Configuration rules and the Alembic migration."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent
GOOD = dict(jwt_secret_key="s" * 40, database_url="sqlite:///:memory:")


def make(**kw):
    return Settings(_env_file=None, **{**GOOD, **kw})


def test_secret_is_required_and_has_no_default(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite:///:memory:")


@pytest.mark.parametrize("secret", ["short", "x" * 31, ""])
def test_short_secret_rejected(secret):
    with pytest.raises(ValidationError):
        make(jwt_secret_key=secret)


def test_algorithm_must_be_hmac():
    for alg in ("none", "RS256", "hs256"):
        with pytest.raises(ValidationError):
            make(jwt_algorithm=alg)
    assert make(jwt_algorithm="HS512").jwt_algorithm == "HS512"


def test_database_settings_must_be_complete(monkeypatch):
    for name in ("DATABASE_URL", "MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, jwt_secret_key="s" * 40)
    assert "MYSQL_PASSWORD" in str(e.value)


def test_mysql_url_is_built_from_settings_and_quotes_passwords(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = Settings(_env_file=None, jwt_secret_key="s" * 40, mysql_host="catalog-db", mysql_port=3306, mysql_database="shopease_product_catalog_db",
                 mysql_user="shopease_catalog", mysql_password="p@ss:w/rd")
    assert s.sqlalchemy_url == "mysql+pymysql://shopease_catalog:p%40ss%3Aw%2Frd@catalog-db:3306/shopease_product_catalog_db"
    assert "p@ss" not in repr(s) and "s" * 40 not in repr(s)  # secrets are masked


def test_low_stock_threshold_and_admin_role_are_configurable():
    assert make(low_stock_threshold=25, admin_role="manager").low_stock_threshold == 25
    with pytest.raises(ValidationError):
        make(low_stock_threshold=-1)


def test_no_hardcoded_secrets_in_source():
    banned = ("password=", "secret_key=", "mysql+pymysql://root")
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text().lower().replace(" ", "")
        for needle in banned:
            assert needle not in text or "environ" in text or "settings" in text, f"{path}: {needle}"
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
    assert {"categories", "products", "inventory_transactions", "alembic_version"} <= tables
    r = run_alembic(db, "check")
    assert r.returncode == 0 and "No new upgrade operations" in (r.stdout + r.stderr), r.stdout + r.stderr
    cols = {row[1] for row in sqlite3.connect(db).execute("pragma table_info(products)")}
    assert {"category_id", "name", "description", "sku", "price", "discount_price", "stock_quantity", "image_url", "brand", "is_active", "created_at", "updated_at"} <= cols
    r = run_alembic(db, "downgrade", "base")
    assert r.returncode == 0, r.stderr
    left = {row[0] for row in sqlite3.connect(db).execute("select name from sqlite_master where type='table'")}
    assert not ({"categories", "products", "inventory_transactions"} & left)


def test_docker_files_follow_the_requirements():
    import yaml
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    svc, db = compose["services"]["catalog-service"], compose["services"]["catalog-db"]
    assert "healthcheck" in db and svc["depends_on"]["catalog-db"]["condition"] == "service_healthy"
    assert svc["environment"]["MYSQL_HOST"] == "catalog-db" and "catalog_db_data" in compose["volumes"]
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "python:3.11" in dockerfile and "alembic upgrade head" in dockerfile and "USER" in dockerfile
