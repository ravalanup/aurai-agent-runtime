"""Loading agents.yaml and approvers.yaml, with the checks that catch a misconfigured agent at startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

TIERS = ("T0", "T1", "T2", "T3")
FRAMEWORKS = ("langgraph", "n8n", "claude-agent-sdk", "crewai")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    name: str
    framework: str
    team_id: str
    use_case_id: str
    risk_tier: str
    client_id: str
    client_secret_env: str
    gateway_key_env: str
    model: str
    connectors: dict[str, str]

    def client_secret(self, env: Mapping[str, str] | None = None) -> str:
        value = (os.environ if env is None else env).get(self.client_secret_env, "")
        if not value:
            raise ConfigError(f"agent {self.name}: {self.client_secret_env} is not set")
        return value

    def gateway_key(self, env: Mapping[str, str] | None = None) -> str:
        value = (os.environ if env is None else env).get(self.gateway_key_env, "")
        if not value:
            raise ConfigError(f"agent {self.name}: {self.gateway_key_env} is not set")
        return value


def load_agents(path: Path) -> dict[str, AgentConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    agents: dict[str, AgentConfig] = {}
    for name, spec in (raw.get("agents") or {}).items():
        where = f"{path}: agent {name!r}"
        missing = [
            k
            for k in (
                "framework",
                "team_id",
                "use_case_id",
                "risk_tier",
                "client_id",
                "client_secret_env",
                "gateway_key_env",
                "model",
                "connectors",
            )
            if not spec.get(k)
        ]
        if missing:
            raise ConfigError(f"{where}: missing {', '.join(missing)}")
        if spec["framework"] not in FRAMEWORKS:
            raise ConfigError(f"{where}: framework must be one of {FRAMEWORKS}")
        if spec["risk_tier"] not in TIERS:
            raise ConfigError(f"{where}: risk_tier must be one of {TIERS}")
        if not isinstance(spec["connectors"], dict) or not all(
            str(u).startswith("http") for u in spec["connectors"].values()
        ):
            raise ConfigError(f"{where}: connectors must map a name to an http(s) URL")
        agents[name] = AgentConfig(
            name=name,
            **{
                k: spec[k]
                for k in (
                    "framework",
                    "team_id",
                    "use_case_id",
                    "risk_tier",
                    "client_id",
                    "client_secret_env",
                    "gateway_key_env",
                    "model",
                )
            },
            connectors=dict(spec["connectors"]),
        )
    return agents


def load_approvers(path: Path) -> dict[str, list[str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    teams = raw.get("teams") or {}
    for team, groups in teams.items():
        if not isinstance(groups, list) or not groups or not all(isinstance(g, str) and g for g in groups):
            raise ConfigError(f"{path}: team {team!r} needs a non-empty list of approver groups")
    return {t: list(g) for t, g in teams.items()}


def check_consistency(agents: dict[str, AgentConfig], approvers: dict[str, list[str]]) -> list[str]:
    """Every agent that can need approval has someone who can give it, and ids are unique."""
    problems = []
    for agent in agents.values():
        if agent.team_id not in approvers:
            problems.append(f"agent {agent.name}: team {agent.team_id!r} has no approver groups in approvers.yaml")
    clients = [a.client_id for a in agents.values()]
    if len(clients) != len(set(clients)):
        problems.append("two agents share a Keycloak client_id")
    use_cases = [a.use_case_id for a in agents.values()]
    if len(use_cases) != len(set(use_cases)):
        problems.append("two agents share a use_case_id")
    return problems
