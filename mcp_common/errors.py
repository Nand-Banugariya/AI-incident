"""The fixed error vocabulary every MCP tool reports with.

Tools raise a `ToolError` subclass for EXPECTED failures (bad input, missing file, blocked path). `@safe_tool`
turns it into `ok: false` with the error's code. Anything else that escapes a tool is mapped to a code by
`@safe_tool` itself, and its message is never shown to the caller.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    INVALID_ARGUMENT = "invalid_argument"
    PATH_NOT_ALLOWED = "path_not_allowed"
    EXTENSION_UNAVAILABLE = "extension_unavailable"
    RESULT_TOO_LARGE = "result_too_large"
    INTERNAL = "internal"


# A caller may sensibly retry these; everything else will fail the same way again
RETRYABLE_CODES = frozenset({ErrorCode.TIMEOUT, ErrorCode.UNREACHABLE})


class ToolError(Exception):
    """An expected failure. Its message IS shown to the caller, so it must never contain a secret or host path."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(self, message: str, *, retryable: bool | None = None):
        super().__init__(message)
        self.message = message
        self.retryable = self.code in RETRYABLE_CODES if retryable is None else retryable


class Unreachable(ToolError):
    code = ErrorCode.UNREACHABLE


class PermissionDenied(ToolError):
    code = ErrorCode.PERMISSION_DENIED


class NotFound(ToolError):
    code = ErrorCode.NOT_FOUND


class InvalidArgument(ToolError):
    code = ErrorCode.INVALID_ARGUMENT


class PathNotAllowed(ToolError):
    code = ErrorCode.PATH_NOT_ALLOWED

    def __init__(self, message: str = "Path is outside the allowed roots"):
        super().__init__(message)


class ExtensionUnavailable(ToolError):
    code = ErrorCode.EXTENSION_UNAVAILABLE


class ResultTooLarge(ToolError):
    code = ErrorCode.RESULT_TOO_LARGE
