"""What an agent uses to talk to the approval service."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from .errors import ApprovalError
from .tokens import TokenProvider


class ApprovalsAPI(Protocol):
    def create(self, **fields: Any) -> dict: ...

    def get(self, approval_id: str) -> dict: ...

    def token(self, approval_id: str) -> str: ...


class HttpApprovalClient:
    def __init__(self, base_url: str, tokens: TokenProvider, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._tokens = tokens
        self._client = client or httpx.Client(timeout=15)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        response = self._client.request(
            method, self._base + path, headers={"Authorization": f"Bearer {self._tokens.token()}"}, **kwargs
        )
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text) if response.content else response.reason_phrase
            raise ApprovalError(response.status_code, str(detail))
        return response.json()

    def create(self, **fields: Any) -> dict:
        return self._request("POST", "/v1/approvals", json=fields)

    def get(self, approval_id: str) -> dict:
        return self._request("GET", f"/v1/approvals/{approval_id}")

    def token(self, approval_id: str) -> str:
        return self._request("POST", f"/v1/approvals/{approval_id}/token")["token"]
