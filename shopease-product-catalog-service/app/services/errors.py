"""Business errors. The exception handler in main.py turns them into the common JSON error format."""


class ServiceError(Exception):
    status_code = 400
    error = "Bad Request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    status_code, error = 404, "Not Found"


class ConflictError(ServiceError):
    status_code, error = 409, "Conflict"


class UnprocessableError(ServiceError):
    status_code, error = 422, "Validation Error"
