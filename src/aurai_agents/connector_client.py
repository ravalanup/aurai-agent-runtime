"""A small MCP client for the Aurai connectors. They are stateless streamable-HTTP servers that answer with JSON, so a
tool call is one JSON-RPC POST. The agent's own service token goes in Authorization; a signed approval, when it has
one, goes in X-Aurai-Approval (never in the arguments)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .errors import ApprovalNeeded, ConnectorRefused
from .tokens import TokenProvider

APPROVAL_HEADER = "X-Aurai-Approval"


class ConnectorAuthError(ConnectorRefused):
    def __init__(self, status: int) -> None:
        super().__init__("unauthenticated", f"the connector rejected the service token (HTTP {status})")


class ConnectorClient:
    def __init__(self, urls: dict[str, str], tokens: TokenProvider, client: httpx.Client | None = None) -> None:
        self._urls, self._tokens = urls, tokens
        self._client = client or httpx.Client(timeout=30)
        self._id = 0

    def call(self, connector: str, tool: str, arguments: dict[str, Any], approval: str | None = None) -> Any:
        if connector not in self._urls:
            raise ConnectorRefused("unknown_connector", f"no URL is configured for connector {connector!r}")
        headers = {
            "Authorization": f"Bearer {self._tokens.token()}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if approval:
            headers[APPROVAL_HEADER] = approval
        self._id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        response = self._client.post(self._urls[connector], headers=headers, content=json.dumps(body))
        if response.status_code in (401, 403):
            raise ConnectorAuthError(response.status_code)
        response.raise_for_status()
        message = response.json()
        if "error" in message:
            raise ConnectorRefused("protocol_error", str(message["error"].get("message", "protocol error")))
        result = message["result"]
        text = "".join(block.get("text", "") for block in result.get("content", []))
        if result.get("isError"):
            raise _refusal(text, tool)
        structured = result.get("structuredContent")
        if structured is not None:
            return structured["result"] if set(structured) == {"result"} else structured
        return json.loads(text) if text else None


def _refusal(text: str, tool: str) -> ConnectorRefused:
    """The connector's errors are a JSON object after the SDK's 'Error executing tool x:' prefix."""
    try:
        detail = json.loads(text[text.index("{") :])
    except ValueError:
        return ConnectorRefused("tool_error", "the connector returned an error")
    code, message = str(detail.get("error", "tool_error")), str(detail.get("message", ""))
    if code == "approval_required":
        return ApprovalNeeded(
            message, int(detail.get("approvals_required") or 1), str(detail.get("args_hash", "")), tool
        )
    return ConnectorRefused(code, message, detail)
