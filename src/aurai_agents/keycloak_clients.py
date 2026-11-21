"""The Keycloak clients the agent platform needs, as code.

Each agent gets its own confidential client with a service account. Its access tokens carry the agent's team and use
case as hardcoded claims, so a connector or the approval service learns who is calling from a claim the agent cannot
change, and the audiences connectors and the approval service require. People who approve actions or start runs use
one public client (authorization code with PKCE) whose tokens carry their groups and the audiences of the runtime and
approval service.

`plan` compares this desired state with the realm and lists what would change; `apply` makes the changes. Secrets are
never printed. A new client's secret comes from the environment variable the agent's config names."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import AgentConfig

CONNECTORS_AUDIENCE = "aurai-connectors"
APPROVALS_AUDIENCE = "aurai-approvals"
AGENTS_AUDIENCE = "aurai-agents"
CONSOLE_CLIENT_ID = "agents-console"
MANAGED_FIELDS = (
    "name",
    "enabled",
    "publicClient",
    "serviceAccountsEnabled",
    "standardFlowEnabled",
    "directAccessGrantsEnabled",
    "implicitFlowEnabled",
    "redirectUris",
    "webOrigins",
)


def _mapper(kind: str, name: str, **config: str) -> dict[str, Any]:
    return {"name": name, "protocol": "openid-connect", "protocolMapper": kind, "config": config}


def hardcoded_claim(name: str, value: str) -> dict[str, Any]:
    return _mapper(
        "oidc-hardcoded-claim-mapper",
        name,
        **{
            "claim.name": name,
            "claim.value": value,
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "false",
            "userinfo.token.claim": "false",
        },
    )


def audience(value: str) -> dict[str, Any]:
    return _mapper(
        "oidc-audience-mapper",
        f"aud-{value}",
        **{"included.custom.audience": value, "access.token.claim": "true", "id.token.claim": "false"},
    )


def groups_claim() -> dict[str, Any]:
    return _mapper(
        "oidc-group-membership-mapper",
        "groups",
        **{
            "claim.name": "groups",
            "full.path": "false",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )


@dataclass(frozen=True)
class ClientSpec:
    client_id: str
    name: str
    fields: dict[str, Any]
    mappers: list[dict[str, Any]]
    secret_env: str = ""

    def representation(self, secret: str = "") -> dict[str, Any]:
        body = {"clientId": self.client_id, **self.fields, "protocolMappers": self.mappers}
        if secret:
            body["secret"] = secret
        return body


def agent_client(agent: AgentConfig) -> ClientSpec:
    return ClientSpec(
        client_id=agent.client_id,
        name=f"Agent: {agent.name}",
        secret_env=agent.client_secret_env,
        fields={
            "name": f"Agent: {agent.name}",
            "enabled": True,
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "implicitFlowEnabled": False,
            "redirectUris": [],
            "webOrigins": [],
        },
        mappers=[
            hardcoded_claim("team_id", agent.team_id),
            hardcoded_claim("use_case_id", agent.use_case_id),
            audience(CONNECTORS_AUDIENCE),
            audience(APPROVALS_AUDIENCE),
        ],
    )


def console_client(redirect_uris: Iterable[str] = ("http://localhost:3200/*",)) -> ClientSpec:
    uris = list(redirect_uris)
    return ClientSpec(
        client_id=CONSOLE_CLIENT_ID,
        name="Agents console",
        fields={
            "name": "Agents console",
            "enabled": True,
            "publicClient": True,
            "serviceAccountsEnabled": False,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "implicitFlowEnabled": False,
            "redirectUris": uris,
            "webOrigins": ["+"],
            "attributes": {"pkce.code.challenge.method": "S256"},
        },
        mappers=[groups_claim(), audience(AGENTS_AUDIENCE), audience(APPROVALS_AUDIENCE)],
    )


def orchestrator_client(client_id: str = "n8n-orchestrator", secret_env: str = "AURAI_N8N_CLIENT_SECRET") -> ClientSpec:
    """For workflow tools such as n8n. It may start and resume runs through the runtime API and nothing else: its
    tokens carry the runtime's audience only, so connectors and the approval service refuse them."""
    return ClientSpec(
        client_id=client_id,
        name="Workflow orchestrator (n8n)",
        secret_env=secret_env,
        fields={
            "name": "Workflow orchestrator (n8n)",
            "enabled": True,
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "implicitFlowEnabled": False,
            "redirectUris": [],
            "webOrigins": [],
        },
        mappers=[audience(AGENTS_AUDIENCE)],
    )


def desired_clients(agents: Iterable[AgentConfig]) -> list[ClientSpec]:
    return [console_client(), orchestrator_client(), *(agent_client(a) for a in agents)]


# ---- plan ----


def _normal_mappers(mappers: Iterable[Mapping[str, Any]]) -> list[tuple]:
    out = []
    for m in mappers:
        config = {k: str(v) for k, v in (m.get("config") or {}).items()}
        out.append((m["name"], m["protocolMapper"], tuple(sorted(config.items()))))
    return sorted(out)


def differences(spec: ClientSpec, existing: Mapping[str, Any]) -> list[str]:
    changed = []
    for key in MANAGED_FIELDS:
        if key in spec.fields and spec.fields[key] != existing.get(key):
            changed.append(key)
    for key, value in (spec.fields.get("attributes") or {}).items():
        if (existing.get("attributes") or {}).get(key) != value:
            changed.append(f"attributes.{key}")
    # compare only the config keys we set; Keycloak adds defaults of its own
    have = {m["name"]: m for m in existing.get("protocolMappers") or []}
    for wanted in spec.mappers:
        found = have.get(wanted["name"])
        if not found or found["protocolMapper"] != wanted["protocolMapper"]:
            changed.append(f"mapper:{wanted['name']}")
            continue
        if any(str((found.get("config") or {}).get(k)) != str(v) for k, v in wanted["config"].items()):
            changed.append(f"mapper:{wanted['name']}")
    return changed


@dataclass
class Plan:
    create: list[ClientSpec] = field(default_factory=list)
    update: list[tuple[ClientSpec, dict[str, Any], list[str]]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "create": [s.client_id for s in self.create],
            "update": {s.client_id: changed for s, _, changed in self.update},
            "unchanged": self.unchanged,
        }


def make_plan(desired: Iterable[ClientSpec], existing: Iterable[Mapping[str, Any]]) -> Plan:
    by_id = {c["clientId"]: c for c in existing}
    plan = Plan()
    for spec in desired:
        current = by_id.get(spec.client_id)
        if current is None:
            plan.create.append(spec)
            continue
        changed = differences(spec, current)
        if changed:
            plan.update.append((spec, dict(current), changed))
        else:
            plan.unchanged.append(spec.client_id)
    return plan


# ---- Keycloak admin API ----


class KeycloakAdmin:
    def __init__(self, base_url: str, realm: str, user: str, password: str, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._user, self._password = user, password
        self._client = client or httpx.Client(timeout=30)
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            response = self._client.post(
                f"{self._base}/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": self._user,
                    "password": self._password,
                },
            )
            response.raise_for_status()
            self._token = response.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str) -> str:
        return f"{self._base}/admin/realms/{self._realm}{path}"

    def list_clients(self) -> list[dict[str, Any]]:
        response = self._client.get(self._url("/clients"), headers=self._headers(), params={"max": 1000})
        response.raise_for_status()
        return response.json()

    def create_client(self, body: dict[str, Any]) -> None:
        self._client.post(self._url("/clients"), headers=self._headers(), json=body).raise_for_status()

    def update_client(self, internal_id: str, body: dict[str, Any]) -> None:
        self._client.put(self._url(f"/clients/{internal_id}"), headers=self._headers(), json=body).raise_for_status()


def apply_plan(admin: KeycloakAdmin, plan: Plan, env: Mapping[str, str]) -> None:
    for spec in plan.create:
        secret = env.get(spec.secret_env, "") if spec.secret_env else ""
        if spec.secret_env and not secret:
            raise ValueError(f"{spec.secret_env} must be set to create client {spec.client_id}")
        admin.create_client(spec.representation(secret))
    for spec, current, _ in plan.update:
        body = {**current, **{k: v for k, v in spec.fields.items() if k != "attributes"}}
        body["attributes"] = {**(current.get("attributes") or {}), **(spec.fields.get("attributes") or {})}
        have = {m["name"]: m for m in current.get("protocolMappers") or []}
        merged = [m for n, m in have.items() if n not in {w["name"] for w in spec.mappers}]
        body["protocolMappers"] = merged + spec.mappers
        admin.update_client(current["id"], body)
