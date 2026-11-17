"""Business errors. The exception handlers in main.py turn them into the uniform JSON error response."""


class ServiceError(Exception):
    status_code, error = 400, "Bad Request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    status_code, error = 404, "Not Found"


class ConflictError(ServiceError):
    status_code, error = 409, "Conflict"


class UnprocessableError(ServiceError):
    status_code, error = 422, "Unprocessable Entity"


class BadGatewayError(ServiceError):
    """Another service answered, but not in a usable way (for example it rejected our service token)."""
    status_code, error = 502, "Bad Gateway"


class ServiceUnavailableError(ServiceError):
    """Another service could not be reached (connection error, timeout, 5xx) -> HTTP 503."""
    status_code, error = 503, "Service Unavailable"


# ---- errors raised by the clients; the order logic decides how to react to each of them
class ProductNotFoundError(Exception):
    pass


class InsufficientStockError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
