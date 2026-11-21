"""HTTP API of the approval service.

Agents (service accounts):   POST /v1/approvals                  create, idempotent
                             GET  /v1/approvals/{id}             status
                             POST /v1/approvals/{id}/token       collect the signed token once approved
Approvers (people):          GET  /v1/approvals?status=pending   what is waiting for their team
                             GET  /v1/approvals/{id}/review      the exact call to review
                             POST /v1/approvals/{id}/decision    approve or reject
Both present Keycloak tokens for the audience aurai-approvals."""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .approval_service import ApprovalService, CreateRequest
from .errors import ApprovalError, AuthError
from .verify import Principal, TokenVerifier, bearer


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str
    connector: str
    tool: str
    arguments: dict
    team: str
    use_case: str
    summary: str
    required: int = Field(ge=1)
    on_behalf_of: str | None = None


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    comment: str = ""


def create_app(service: ApprovalService, verifier: TokenVerifier) -> FastAPI:
    app = FastAPI(title="Aurai approval service", version="0.1.0")

    def principal(authorization: str | None = Header(default=None)) -> Principal:
        try:
            return verifier.verify(bearer(authorization))
        except AuthError as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

    def guard(call):
        try:
            return call()
        except ApprovalError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/v1/approvals", status_code=201)
    def create(body: CreateBody, who: Principal = Depends(principal)) -> dict:
        approval = guard(lambda: service.create(who, CreateRequest(**body.model_dump())))
        return service.view(approval)

    @app.get("/v1/approvals")
    def list_pending(who: Principal = Depends(principal)) -> dict:
        return {"approvals": [service.view(a) for a in service.list_pending(who)]}

    @app.get("/v1/approvals/{approval_id}")
    def get(approval_id: str, who: Principal = Depends(principal)) -> dict:
        return service.view(guard(lambda: service.get_for_agent(who, approval_id)))

    @app.post("/v1/approvals/{approval_id}/token")
    def token(approval_id: str, who: Principal = Depends(principal)) -> dict:
        return {"token": guard(lambda: service.mint_token(who, approval_id))}

    @app.get("/v1/approvals/{approval_id}/review")
    def review(approval_id: str, who: Principal = Depends(principal)) -> dict:
        return service.view(guard(lambda: service.get_for_approver(who, approval_id)))

    @app.post("/v1/approvals/{approval_id}/decision")
    def decide(approval_id: str, body: DecisionBody, who: Principal = Depends(principal)) -> dict:
        return service.view(guard(lambda: service.decide(who, approval_id, body.decision, body.comment)))

    return app
