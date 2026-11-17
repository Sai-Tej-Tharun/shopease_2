"""Configuration rules and the Alembic migration."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent
GOOD_SECRET = "s" * 40


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


def test_secrets_have_no_defaults(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY")
    with pytest.raises(ValidationError):
        settings(database_url="sqlite://")


def test_short_jwt_secret_rejected():
    with pytest.raises(ValidationError, match="at least 32"):
        settings(database_url="sqlite://", jwt_secret_key="short")


def test_unsafe_jwt_algorithm_rejected():
    with pytest.raises(ValidationError):
        settings(database_url="sqlite://", jwt_secret_key=GOOD_SECRET, jwt_algorithm="none")


def test_missing_database_settings_rejected(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValidationError, match="MYSQL_HOST"):
        settings(jwt_secret_key=GOOD_SECRET)


def test_mysql_url_is_built_from_settings_with_encoding(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    s = settings(jwt_secret_key=GOOD_SECRET, mysql_host="user-db", mysql_database="shopease_user_db",
                 mysql_user="shopease_user", mysql_password="p@ss/word", auth_service_url="http://auth:8001/")
    assert s.sqlalchemy_url == "mysql+pymysql://shopease_user:p%40ss%2Fword@user-db:3306/shopease_user_db"
    assert s.auth_service_url == "http://auth:8001" and "p@ss" not in repr(s)


def test_alembic_migration_creates_both_tables_and_drops_them(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    insp = inspect(engine)
    profiles = {c["name"] for c in insp.get_columns("user_profiles")}
    addresses = {c["name"] for c in insp.get_columns("user_addresses")}
    assert profiles == {"id", "auth_user_id", "full_name", "phone_number", "date_of_birth", "gender", "profile_image",
                        "is_active", "created_at", "updated_at"}
    assert addresses == {"id", "user_id", "address_type", "full_name", "phone_number", "address_line_1", "address_line_2",
                         "city", "state", "country", "postal_code", "is_default", "created_at"}
    assert any(u["column_names"] == ["auth_user_id"] for u in insp.get_unique_constraints("user_profiles"))
    fk = insp.get_foreign_keys("user_addresses")[0]
    assert fk["referred_table"] == "user_profiles" and fk["constrained_columns"] == ["user_id"]

    command.check(cfg)  # raises if the models and the migration disagree
    command.downgrade(cfg, "base")
    assert not {"user_profiles", "user_addresses"} & set(inspect(engine).get_table_names())
