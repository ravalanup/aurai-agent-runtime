"""HTTP API of the runtime.

    POST /v1/agents/{agent}/runs                 start a run (returns when it finishes or waits for approval)
    GET  /v1/agents/{agent}/runs/{run_id}        status, approvals and result
    POST /v1/agents/{agent}/runs/{run_id}/resume re-check the approvals and carry on

People call it with Keycloak tokens for the audience aurai-agents. A run belongs to whoever started it."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from .errors import AuthError
from .runtime import RunNotFound, Runtime
from .verify import Principal, TokenVerifier, bearer


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: dict = {}


def create_app(runtime: Runtime, verifier: TokenVerifier) -> FastAPI:
    app = FastAPI(title="Aurai agent runtime", version="0.1.0")

    def principal(authorization: str | None = Header(default=None)) -> Principal:
        try:
            return verifier.verify(bearer(authorization))
        except AuthError as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "agents": runtime.names()}

    @app.post("/v1/agents/{agent}/runs", status_code=201)
    def start(agent: str, body: StartBody, who: Principal = Depends(principal)) -> dict:
        if agent not in runtime.names():
            raise HTTPException(404, "no such agent")
        try:
            return runtime.start(agent, body.input, who.subject)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/agents/{agent}/runs/{run_id}")
    def get(agent: str, run_id: str, who: Principal = Depends(principal)) -> dict:
        try:
            return runtime.view(agent, run_id, who.subject)
        except RunNotFound as exc:
            raise HTTPException(404, "no such run") from exc

    @app.post("/v1/agents/{agent}/runs/{run_id}/resume")
    def resume(agent: str, run_id: str, who: Principal = Depends(principal)) -> dict:
        try:
            return runtime.resume(agent, run_id, who.subject)
        except RunNotFound as exc:
            raise HTTPException(404, "no such run") from exc

    return app
