"""Configuration from environment variables (set by the Helm chart)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    issuer: str
    jwks_url: str
    token_url: str
    agents_audience: str
    approvals_audience: str
    approval_signing_key: str
    approval_service_url: str
    database_url: str
    otlp_endpoint: str
    config_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        issuer = e.get("AURAI_ISSUER", "http://keycloak.aurai-system.svc.cluster.local:8080/realms/aurai").rstrip("/")
        return cls(
            issuer=issuer,
            jwks_url=e.get("AURAI_JWKS_URL", issuer + "/protocol/openid-connect/certs"),
            token_url=e.get("AURAI_TOKEN_URL", issuer + "/protocol/openid-connect/token"),
            agents_audience=e.get("AURAI_AGENTS_AUDIENCE", "aurai-agents"),
            approvals_audience=e.get("AURAI_APPROVALS_AUDIENCE", "aurai-approvals"),
            approval_signing_key=e.get("AURAI_APPROVAL_SIGNING_KEY", ""),
            approval_service_url=e.get(
                "AURAI_APPROVAL_SERVICE_URL", "http://approvals.aurai-agents.svc.cluster.local:8080"
            ),
            database_url=e.get("AURAI_DATABASE_URL", ""),
            otlp_endpoint=e.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
            config_dir=Path(e.get("AURAI_CONFIG_DIR", "config")),
        )
