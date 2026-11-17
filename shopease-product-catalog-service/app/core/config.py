"""Application settings – every value comes from environment variables or the .env file.
Nothing secret has a default value: the service refuses to start without database credentials and a JWT secret."""
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ShopEase Product Catalog Service"
    app_version: str = "1.0.0"
    debug: bool = False

    # ---- database (MySQL). DATABASE_URL can override everything (used by the automated tests)
    database_url: str | None = None
    mysql_host: str | None = None
    mysql_port: int = 3306
    mysql_database: str | None = None
    mysql_user: str | None = None
    mysql_password: SecretStr | None = None

    # ---- token verification: tokens are issued by the Authentication Service and signed with the shared secret
    jwt_secret_key: SecretStr = Field(..., description="Same value as in the Authentication Service")
    jwt_algorithm: str = "HS256"
    admin_role: str = "admin"  # value of the "role" claim that may manage products, categories and inventory

    # ---- optional: also ask the Authentication Service whether the account is still active
    auth_verify_remote: bool = False
    auth_service_url: str = "http://localhost:8001"
    auth_service_timeout: float = Field(default=5.0, gt=0)

    # ---- inventory
    low_stock_threshold: int = Field(default=10, ge=0, le=1_000_000)  # default limit for /api/inventory/low-stock

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("jwt_secret_key")
    @classmethod
    def secret_must_be_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def algorithm_must_be_hmac(cls, value: str) -> str:
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be HS256, HS384 or HS512")
        return value

    @field_validator("auth_service_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def database_settings_must_be_complete(self):
        if not self.database_url:
            missing = [n for n in ("mysql_host", "mysql_database", "mysql_user", "mysql_password") if not getattr(self, n)]
            if missing:
                raise ValueError("Missing database settings: " + ", ".join(m.upper() for m in missing))
        return self

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password.get_secret_value())
        return f"mysql+pymysql://{user}:{password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
