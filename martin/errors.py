class AppError(Exception):
    status_code = 400

    def __init__(self, code: str, message: str, status_code: int | None = None, payload: dict | None = None):
        self.code = code
        self.message = message
        self.payload = payload or {}
        if status_code is not None:
            self.status_code = status_code
        super().__init__(message)


def unauthorized(msg: str = "invalid or missing token") -> AppError:
    return AppError("unauthorized", msg, 401)


def not_found(msg: str = "not found") -> AppError:
    return AppError("not_found", msg, 404)


def forbidden(msg: str = "forbidden") -> AppError:
    return AppError("forbidden", msg, 403)


def bad_request(msg: str, code: str = "bad_request") -> AppError:
    return AppError(code, msg, 400)
