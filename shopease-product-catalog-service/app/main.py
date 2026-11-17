"""Creates the FastAPI app, registers the routers and the exception handlers."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.security import AccessDeniedError, AuthenticationError, AuthServiceUnavailableError
from app.routers import category_routes, inventory_routes, product_routes
from app.services.errors import ServiceError

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("catalog-service")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Products, categories and inventory for ShopEase. Log in with the Authentication Service, click **Authorize** "
                "and paste the `access_token`. Customers can view active products and categories; administrators manage everything.",
)
app.include_router(product_routes.router)
app.include_router(category_routes.router)
app.include_router(inventory_routes.router)


def error_response(status_code: int, error: str, message: str, details: list[dict] | None = None,
                   headers: dict | None = None) -> JSONResponse:
    body = {"status_code": status_code, "error": error, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content=body, headers=headers)


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(request: Request, exc: AuthenticationError):
    return error_response(401, "Unauthorized", exc.message, headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(AccessDeniedError)
async def access_denied_handler(request: Request, exc: AccessDeniedError):
    return error_response(403, "Forbidden", exc.message)


@app.exception_handler(AuthServiceUnavailableError)
async def auth_unavailable_handler(request: Request, exc: AuthServiceUnavailableError):
    return error_response(503, "Service Unavailable", "The Authentication Service is not reachable. Please try again later.")


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return error_response(exc.status_code, exc.error, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    details = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err["loc"] if part not in ("body", "query", "path")) or "body"
        if err["type"] == "missing":
            message = "Field is required"
        elif err["type"] == "extra_forbidden":
            message = "Field is not allowed"
        else:
            message = err["msg"].removeprefix("Value error, ")
        details.append({"field": field, "message": message})  # submitted values are never echoed back
    return error_response(422, "Validation Error", "Invalid request data", details)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    title = {404: "Not Found", 405: "Method Not Allowed"}.get(exc.status_code, "Error")
    return error_response(exc.status_code, title, str(exc.detail))


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError):
    logger.exception("Database error on %s %s", request.method, request.url.path)  # the session was rolled back
    return error_response(500, "Internal Server Error", "A database error occurred. Please try again later.")


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.exception("Unexpected error on %s %s", request.method, request.url.path)
    return error_response(500, "Internal Server Error", "An unexpected error occurred. Please try again later.")


@app.get("/health", tags=["General"], summary="Health check")
def health():
    return {"status": "ok", "service": settings.app_name}
