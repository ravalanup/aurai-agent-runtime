"""Errors shared across the runtime."""

from __future__ import annotations


class AuthError(Exception):
    """The token is missing, malformed, expired or not meant for this service."""


class ApprovalError(Exception):
    """A refusal by the approval service. `status_code` is the HTTP status the API returns."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ConnectorRefused(Exception):
    """A connector returned an error result. `code` is its machine-readable error, e.g. policy_denied."""

    def __init__(self, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail or {}


class ApprovalNeeded(ConnectorRefused):
    """The connector wants a human approval for this exact call."""

    def __init__(self, message: str, approvals_required: int, args_hash: str, tool: str) -> None:
        super().__init__("approval_required", message, {"approvals_required": approvals_required})
        self.approvals_required = approvals_required
        self.args_hash = args_hash
        self.tool = tool
