"""Outbound identity: an agent acts as its own Keycloak service account (client credentials), one per use case. The
token's team_id and use_case_id claims are set by mappers on that client, so an agent cannot claim another team's."""

from __future__ import annotations

import time
from typing import Protocol

import httpx


class TokenProvider(Protocol):
    def token(self) -> str: ...


class StaticTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token


class ClientCredentialsProvider:
    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        client: httpx.Client | None = None,
        clock=time.time,
        skew_seconds: int = 30,
    ) -> None:
        self._url, self._id, self._secret = token_url, client_id, client_secret
        self._client = client or httpx.Client(timeout=10)
        self._clock, self._skew = clock, skew_seconds
        self._cached: tuple[str, float] | None = None

    def token(self) -> str:
        now = self._clock()
        if self._cached and self._cached[1] - self._skew > now:
            return self._cached[0]
        response = self._client.post(
            self._url,
            data={"grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret},
        )
        response.raise_for_status()
        body = response.json()
        token = body["access_token"]
        self._cached = (token, now + float(body.get("expires_in", 60)))
        return token
