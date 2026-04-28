"""Shared exception types and error serialization."""

from __future__ import annotations


class SysbenchDevicesError(Exception):
    """Base class for domain errors."""

    code = "error"
    http_status = 400


class NotFoundError(SysbenchDevicesError):
    code = "not_found"
    http_status = 404


class ConflictError(SysbenchDevicesError):
    code = "conflict"
    http_status = 409


class ValidationError(SysbenchDevicesError):
    code = "validation_error"
    http_status = 400


class AuthenticationError(SysbenchDevicesError):
    code = "authentication_error"
    http_status = 401


class HardwareError(SysbenchDevicesError):
    code = "hardware_error"
    http_status = 502


def error_response(exc: BaseException) -> dict[str, object]:
    if isinstance(exc, SysbenchDevicesError):
        return {"code": exc.code, "message": str(exc)}
    return {"code": "internal_error", "message": str(exc)}
