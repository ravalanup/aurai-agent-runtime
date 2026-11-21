"""Builds the two services from settings: the runtime (agents, checkpointer, clients) and the approval service."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from .approval_client import HttpApprovalClient
from .approval_service import ApprovalService
from .approval_store import ApprovalStore, MemoryApprovalStore, PostgresApprovalStore
from .checkpoint import make_checkpointer
from .config import AgentConfig, ConfigError, check_consistency, load_agents, load_approvers
from .connector_client import ConnectorClient
from .kit import AgentContext
from .model import GatewayModel
from .runtime import BUILDERS, Runtime
from .settings import Settings
from .tokens import ClientCredentialsProvider
from .tracing import setup_tracing
from .verify import TokenVerifier

GATEWAY_URL_ENV = "AURAI_GATEWAY_URL"
DEFAULT_GATEWAY_URL = "http://litellm.aurai-gateway.svc.cluster.local:4000"


def load_config(settings: Settings) -> tuple[dict[str, AgentConfig], dict[str, list[str]]]:
    agents = load_agents(settings.config_dir / "agents.yaml")
    approvers = load_approvers(settings.config_dir / "approvers.yaml")
    problems = check_consistency(agents, approvers)
    problems += [f"agent {n}: no implementation is registered" for n in agents if n not in BUILDERS]
    if problems:
        raise ConfigError("; ".join(problems))
    return agents, approvers


def verifier(settings: Settings, audience: str) -> TokenVerifier:
    return TokenVerifier(settings.issuer, audience, jwks_url=settings.jwks_url)


def build_store(settings: Settings) -> ApprovalStore:
    if not settings.database_url:
        return MemoryApprovalStore()  # single process only: approvals vanish on restart
    store = PostgresApprovalStore(settings.database_url)
    store.setup()
    return store


def build_approval_service(settings: Settings) -> ApprovalService:
    _, approvers = load_config(settings)
    return ApprovalService(build_store(settings), settings.approval_signing_key, approvers)


def build_runtime(settings: Settings, env: Mapping[str, str] | None = None) -> tuple[Runtime, Callable[[], None]]:
    e = os.environ if env is None else env
    agents, _ = load_config(settings)
    tracer = setup_tracing(settings.otlp_endpoint, "aurai-agent-runtime")
    gateway = e.get(GATEWAY_URL_ENV, DEFAULT_GATEWAY_URL)
    contexts = {}
    for name, cfg in agents.items():
        tokens = ClientCredentialsProvider(settings.token_url, cfg.client_id, cfg.client_secret(e))
        contexts[name] = AgentContext(
            config=cfg,
            connectors=ConnectorClient(cfg.connectors, tokens),
            approvals=HttpApprovalClient(settings.approval_service_url, tokens),
            model=GatewayModel(gateway, cfg.gateway_key(e), cfg.model),
            tracer=tracer,
        )
    checkpointer, close = make_checkpointer(settings.database_url)
    return Runtime(contexts, checkpointer), close
