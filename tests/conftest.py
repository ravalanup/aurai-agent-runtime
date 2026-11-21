from __future__ import annotations

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aurai_agents.approval_service import ApprovalService, CreateRequest
from aurai_agents.approval_store import MemoryApprovalStore
from aurai_agents.approval_tokens import args_hash
from aurai_agents.config import AgentConfig
from aurai_agents.errors import ApprovalNeeded, ConnectorRefused
from aurai_agents.kit import AgentContext
from aurai_agents.verify import Principal, TokenVerifier

ISSUER = "http://keycloak.test/realms/aurai"
SIGNING_KEY = "test-approval-signing-key-0123456789abcdef"
APPROVER_GROUPS = {"ops-insights": ["approvers-ops"], "finance-reporting": ["approvers-finance"]}

AGENT = AgentConfig(
    name="ops-insights",
    framework="langgraph",
    team_id="ops-insights",
    use_case_id="ops-insights",
    risk_tier="T2",
    client_id="agent-ops-insights",
    client_secret_env="X",
    gateway_key_env="Y",
    model="aurai-balanced",
    connectors={"tickets": "http://t", "ops-data": "http://o", "messaging": "http://m"},
)

SERVICE = Principal("svc-ops", frozenset(), "ops-insights", "ops-insights")
ALICE = Principal("alice", frozenset({"approvers-ops"}))
BOB = Principal("bob", frozenset({"approvers-ops"}))
CAROL = Principal("carol", frozenset({"approvers-finance"}))
DAVE = Principal("dave", frozenset({"workspace-members"}))


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(clock):
    return ApprovalService(MemoryApprovalStore(), SIGNING_KEY, APPROVER_GROUPS, clock=clock)


def create_request(key="run1:0", arguments=None, required=1, on_behalf_of="erin", **over) -> CreateRequest:
    fields = dict(
        idempotency_key=key,
        connector="tickets",
        tool="tickets_create_draft",
        arguments=arguments or {"title": "Check brake sensor", "body": "Recurring alarm", "area": "Harbor Coaster"},
        team="ops-insights",
        use_case="ops-insights",
        summary="Draft ticket for Harbor Coaster: Check brake sensor",
        required=required,
        on_behalf_of=on_behalf_of,
    )
    fields.update(over)
    return CreateRequest(**fields)


class LocalApprovals:
    """The approval client interface, backed by an in-process ApprovalService as the agent's service account."""

    def __init__(self, service: ApprovalService, caller: Principal = SERVICE) -> None:
        self.service, self.caller = service, caller
        self.created = 0

    def create(self, **fields: Any) -> dict:
        self.created += 1
        return self.service.view(self.service.create(self.caller, CreateRequest(**fields)))

    def get(self, approval_id: str) -> dict:
        return self.service.view(self.service.get_for_agent(self.caller, approval_id))

    def token(self, approval_id: str) -> str:
        return self.service.mint_token(self.caller, approval_id)


class FakeConnectors:
    """Stands in for the connectors: reads return canned data; a ticket draft needs a valid single-use approval."""

    def __init__(self, deny=()):
        self.calls: list[tuple[str, str, dict, bool]] = []
        self.used: set[str] = set()
        self.deny = set(deny)
        self.next_id = 9001
        self.created: list[dict] = []

    def call(self, connector, tool, arguments, approval=None):
        self.calls.append((connector, tool, arguments, approval is not None))
        if tool in self.deny:
            raise ConnectorRefused("policy_denied", "this call is not allowed for your team")
        if tool == "ops_downtime_summary":
            return {"area": arguments["area"], "total_minutes_down": 42, "daily": []}
        if tool == "ops_incidents":
            return [{"date": "2026-11-19", "severity": "medium", "summary": "Synthetic incident."}]
        if tool == "tickets_search":
            return [{"id": "TCK-1001", "title": "Harbor Coaster brake sensor alarm", "status": "open"}]
        if tool == "tickets_create_draft":
            if approval is None:
                raise ApprovalNeeded("1 approval(s) required", 1, args_hash(arguments), tool)
            claims = jwt.decode(approval, SIGNING_KEY, algorithms=["HS256"], options={"verify_exp": False})
            assert claims["tool"] == tool and claims["args_hash"] == args_hash(arguments)
            if claims["jti"] in self.used:
                raise ConnectorRefused("approval_invalid", "approval token was already used")
            self.used.add(claims["jti"])
            self.next_id += 1
            ticket = {"id": f"TCK-{self.next_id}", "status": "draft", **arguments}
            self.created.append(ticket)
            return ticket
        raise AssertionError(f"unexpected call {tool}")


class ScriptedModel:
    alias = "aurai-balanced"

    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


GOOD_REPLY = {
    "briefing": "Harbor Coaster had repeated sensor alarms this week.",
    "proposals": [
        {"area": "Harbor Coaster", "title": "Inspect brake sensor on train 2", "body": "Alarm recurred on three days."},
        {"area": "Lantern Gardens", "title": "Replace east gate path lights", "body": "Reported out for a week."},
    ],
}


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    exporter.tracer = provider.get_tracer("test")
    return exporter


@pytest.fixture
def make_ctx(service, spans):
    def make(reply=None, connectors=None, approvals=None):
        return AgentContext(
            config=AGENT,
            connectors=connectors or FakeConnectors(),
            approvals=approvals or LocalApprovals(service),
            model=ScriptedModel(GOOD_REPLY if reply is None else reply),
            tracer=spans.tracer,
        )

    return make


@pytest.fixture(scope="session")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(private_key):
    return TokenVerifier(ISSUER, "aurai-approvals", key_resolver=lambda _t: private_key.public_key())


def mint_token(
    private_key, sub, audience="aurai-approvals", groups=None, team=None, use_case=None, ttl=300, issuer=ISSUER
):
    now = int(time.time())
    claims: dict[str, Any] = {"iss": issuer, "aud": audience, "sub": sub, "iat": now, "exp": now + ttl}
    if groups is not None:
        claims["groups"] = groups
    if team:
        claims["team_id"] = team
    if use_case:
        claims["use_case_id"] = use_case
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "k"})
