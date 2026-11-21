"""Models are reached only through the Aurai gateway, as any application would: an OpenAI-compatible chat completion
with the use case's virtual key. The gateway applies the team's budget, PII policy and guardrails."""

from __future__ import annotations

from typing import Protocol

import httpx


class ModelClient(Protocol):
    alias: str

    def complete(self, system: str, user: str) -> str: ...


class GatewayModel:
    def __init__(self, base_url: str, key: str, alias: str, client: httpx.Client | None = None) -> None:
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._key = key
        self.alias = alias
        self._client = client or httpx.Client(timeout=120)

    def complete(self, system: str, user: str) -> str:
        response = self._client.post(
            self._url,
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self.alias,
                "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
