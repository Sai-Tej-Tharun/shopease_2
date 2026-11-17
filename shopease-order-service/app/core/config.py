"""Application settings – every value comes from environment variables or the .env file.
Nothing secret has a default value: the service refuses to start without database credentials and a JWT secret."""
from decimal import Decimal
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ShopEase Order Service"
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
    admin_role: str = "admin"  # value of the "role" claim that may see all orders and change order status

    # ---- optional: also ask the Authentication Service whether the account is still active
    auth_verify_remote: bool = False
    auth_service_url: str = "http://localhost:8001"
    auth_service_timeout: float = Field(default=5.0, gt=0)

    # ---- other services (REST). Inside Docker these are host.docker.internal URLs, see docker-compose.yml
    user_service_url: str = "http://localhost:8002"
    product_service_url: str = "http://localhost:8003"
    user_service_timeout: float = Field(default=5.0, gt=0)
    product_service_timeout: float = Field(default=5.0, gt=0)
    service_retry_attempts: int = Field(default=3, ge=1, le=10)  # tries for stock operations (they are idempotent) and reads
    service_account_id: int = Field(default=999999, ge=1)  # "sub" of the Order Service's own token, shown in the Catalog history

    # ---- order limits and pricing rules (all configurable)
    max_items_per_order: int = Field(default=20, ge=1, le=200)
    max_quantity_per_item: int = Field(default=100, ge=1, le=100_000)
    tax_rate: Decimal = Field(default=Decimal("0.18"), ge=0, le=1)  # applied to (subtotal - discount)
    shipping_flat_rate: Decimal = Field(default=Decimal("50.00"), ge=0)  # charged below the free-shipping threshold
    free_shipping_threshold: Decimal = Field(default=Decimal("1000.00"), ge=0)  # (subtotal - discount) at or above this ships free
    order_number_prefix: str = Field(default="ORD", pattern=r"^[A-Z0-9]{2,8}$")

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

    @field_validator("auth_service_url", "user_service_url", "product_service_url")
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
