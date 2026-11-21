"""Test helpers for agent authors. They let an agent be tested end to end, including the approval pause, without a
model, a Keycloak or a connector server: a scripted model, connectors that enforce the real approval protocol, and an
approval client backed by an in-process approval service."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import jwt

from .approval_service import ApprovalService, CreateRequest
from .approval_tokens import args_hash
from .errors import ApprovalNeeded, ConnectorRefused
from .verify import Principal


class ScriptedModel:
    alias = "scripted"

    def __init__(self, reply: str | dict | list) -> None:
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


class LocalApprovals:
    """The approval client interface, backed by an ApprovalService in this process and acting as the agent."""

    def __init__(self, service: ApprovalService, caller: Principal) -> None:
        self.service, self.caller = service, caller
        self.created = 0

    def create(self, **fields: Any) -> dict:
        self.created += 1
        return self.service.view(self.service.create(self.caller, CreateRequest(**fields)))

    def get(self, approval_id: str) -> dict:
        return self.service.view(self.service.get_for_agent(self.caller, approval_id))

    def token(self, approval_id: str) -> str:
        return self.service.mint_token(self.caller, approval_id)


class ScriptedConnectors:
    """Connectors with canned results. `needs_approval` maps a tool to the approvals it requires; such a tool refuses
    until it is given a token signed with `signing_key` for exactly those arguments, and accepts each token once, as a
    real connector does. `responses` maps a tool to a value or to a function of its arguments."""

    def __init__(
        self,
        signing_key: str,
        responses: Mapping[str, Any],
        needs_approval: Mapping[str, int] | None = None,
        deny: tuple[str, ...] = (),
    ) -> None:
        self._key = signing_key
        self._responses = dict(responses)
        self._needs = dict(needs_approval or {})
        self._deny = set(deny)
        self._used: set[str] = set()
        self.calls: list[tuple[str, str, dict, bool]] = []
        self.executed: list[tuple[str, dict]] = []

    def call(self, connector: str, tool: str, arguments: dict, approval: str | None = None) -> Any:
        self.calls.append((connector, tool, arguments, approval is not None))
        if tool in self._deny:
            raise ConnectorRefused("policy_denied", "this call is not allowed for your team")
        if tool in self._needs:
            if approval is None:
                raise ApprovalNeeded(
                    f"{self._needs[tool]} approval(s) required", self._needs[tool], args_hash(arguments), tool
                )
            claims = jwt.decode(approval, self._key, algorithms=["HS256"], options={"verify_exp": False})
            if claims["tool"] != tool or claims["args_hash"] != args_hash(arguments):
                raise ConnectorRefused("approval_invalid", "approval was granted for a different call")
            if claims["jti"] in self._used:
                raise ConnectorRefused("approval_invalid", "approval token was already used")
            self._used.add(claims["jti"])
        if tool not in self._responses:
            raise AssertionError(f"the test did not script a response for {tool}")
        self.executed.append((tool, arguments))
        response: Callable[[dict], Any] | Any = self._responses[tool]
        return response(arguments) if callable(response) else response
