"""Verification of inbound Keycloak tokens: users calling the runtime, approvers calling the approval service, and
agent service accounts calling the approval service. Identity comes only from a verified token."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jwt

from .errors import AuthError


@dataclass(frozen=True)
class Principal:
    subject: str
    groups: frozenset[str] = field(default_factory=frozenset)
    team_id: str | None = None
    use_case_id: str | None = None

    @property
    def is_service(self) -> bool:
        return bool(self.team_id and self.use_case_id)


class TokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        key_resolver: Callable[[str], Any] | None = None,
        leeway_seconds: int = 10,
    ) -> None:
        if not key_resolver and not jwks_url:
            raise ValueError("provide a jwks_url or a key_resolver")
        self._issuer, self._audience, self._leeway = issuer, audience, leeway_seconds
        if key_resolver:
            self._resolve = key_resolver
        else:
            client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600)  # type: ignore[arg-type]
            self._resolve = lambda token: client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                key=self._resolve(token),
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token ({type(exc).__name__})") from exc
        except Exception as exc:  # an unreachable key set must never become a pass
            raise AuthError("token keys could not be resolved") from exc
        team, use_case = claims.get("team_id"), claims.get("use_case_id")
        return Principal(
            subject=str(claims["sub"]),
            groups=frozenset(str(g).strip().lstrip("/") for g in claims.get("groups") or []),
            team_id=team if isinstance(team, str) and team else None,
            use_case_id=use_case if isinstance(use_case, str) and use_case else None,
        )


def bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("missing bearer token")
    token = authorization[7:].strip()
    if not token:
        raise AuthError("missing bearer token")
    return token
