import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from aurai_agents import approval_api, runtime_api
from aurai_agents.runtime import Runtime
from aurai_agents.verify import TokenVerifier
from tests.conftest import ISSUER, mint_token


@pytest.fixture
def approvals_client(service, verifier):
    return TestClient(approval_api.create_app(service, verifier))


def headers(private_key, sub, **kw):
    return {"Authorization": f"Bearer {mint_token(private_key, sub, **kw)}"}


BODY = {
    "idempotency_key": "r1:0",
    "connector": "tickets",
    "tool": "tickets_create_draft",
    "arguments": {"title": "t", "body": "b", "area": "Harbor Coaster"},
    "team": "ops-insights",
    "use_case": "ops-insights",
    "summary": "Draft ticket",
    "required": 1,
    "on_behalf_of": "erin",
}


def agent(private_key):
    return headers(private_key, "svc-ops", team="ops-insights", use_case="ops-insights")


def approver(private_key, sub="alice", groups=("approvers-ops",)):
    return headers(private_key, sub, groups=list(groups))


def test_the_full_http_flow(approvals_client, private_key):
    c = approvals_client
    created = c.post("/v1/approvals", json=BODY, headers=agent(private_key))
    assert created.status_code == 201
    aid = created.json()["id"]
    assert c.post(f"/v1/approvals/{aid}/token", headers=agent(private_key)).status_code == 409  # still pending
    pending = c.get("/v1/approvals", headers=approver(private_key)).json()["approvals"]
    assert [a["id"] for a in pending] == [aid]
    review = c.get(f"/v1/approvals/{aid}/review", headers=approver(private_key)).json()
    assert review["arguments"]["area"] == "Harbor Coaster"
    decided = c.post(
        f"/v1/approvals/{aid}/decision", json={"decision": "approve", "comment": "ok"}, headers=approver(private_key)
    )
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    token = c.post(f"/v1/approvals/{aid}/token", headers=agent(private_key))
    assert token.status_code == 200 and token.json()["token"].count(".") == 2
    assert c.get(f"/v1/approvals/{aid}", headers=agent(private_key)).json()["status"] == "approved"


def test_unauthenticated_and_wrong_audience_are_401(approvals_client, private_key):
    c = approvals_client
    assert c.get("/v1/approvals").status_code == 401
    assert c.get("/v1/approvals", headers={"Authorization": "Bearer junk"}).status_code == 401
    wrong = headers(private_key, "alice", audience="litellm", groups=["approvers-ops"])
    assert c.get("/v1/approvals", headers=wrong).status_code == 401


def test_a_person_cannot_create_and_an_agent_cannot_decide(approvals_client, private_key):
    c = approvals_client
    assert c.post("/v1/approvals", json=BODY, headers=approver(private_key)).status_code == 403
    aid = c.post("/v1/approvals", json=BODY, headers=agent(private_key)).json()["id"]
    r = c.post(f"/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=agent(private_key))
    assert r.status_code == 404  # an agent has no approver group, so the request is invisible to it


def test_non_approvers_and_other_teams_see_nothing(approvals_client, private_key):
    c = approvals_client
    aid = c.post("/v1/approvals", json=BODY, headers=agent(private_key)).json()["id"]
    outsider = approver(private_key, "dave", groups=["workspace-members"])
    assert c.get("/v1/approvals", headers=outsider).json() == {"approvals": []}
    assert c.get(f"/v1/approvals/{aid}/review", headers=outsider).status_code == 404
    other_agent = headers(private_key, "svc-fin", team="finance-reporting", use_case="forecast")
    assert c.get(f"/v1/approvals/{aid}", headers=other_agent).status_code == 404
    assert c.post(f"/v1/approvals/{aid}/token", headers=other_agent).status_code == 404


def test_invalid_bodies_are_422_and_extra_fields_are_refused(approvals_client, private_key):
    c = approvals_client
    assert c.post("/v1/approvals", json={**BODY, "extra": 1}, headers=agent(private_key)).status_code == 422
    assert c.post("/v1/approvals", json={**BODY, "required": 0}, headers=agent(private_key)).status_code == 422
    aid = c.post("/v1/approvals", json=BODY, headers=agent(private_key)).json()["id"]
    r = c.post(f"/v1/approvals/{aid}/decision", json={"decision": "maybe"}, headers=approver(private_key))
    assert r.status_code == 422


def test_self_approval_is_403(approvals_client, private_key):
    c = approvals_client
    body = {**BODY, "on_behalf_of": "alice"}
    aid = c.post("/v1/approvals", json=body, headers=agent(private_key)).json()["id"]
    r = c.post(f"/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=approver(private_key))
    assert r.status_code == 403


def test_healthz_is_open(approvals_client):
    assert approvals_client.get("/healthz").json() == {"status": "ok"}


# ---- the runtime API ----------------------------------------------------------------------------------------------


@pytest.fixture
def runtime_client(make_ctx, private_key):
    verifier = TokenVerifier(ISSUER, "aurai-agents", key_resolver=lambda _t: private_key.public_key())
    runtime = Runtime({"ops-insights": make_ctx()}, InMemorySaver())
    return TestClient(runtime_api.create_app(runtime, verifier))


def user(private_key, sub="erin"):
    return headers(private_key, sub, audience="aurai-agents", groups=["workspace-members"])


def test_start_get_and_resume_over_http(runtime_client, private_key):
    c = runtime_client
    started = c.post(
        "/v1/agents/ops-insights/runs", json={"input": {"areas": ["Harbor Coaster"]}}, headers=user(private_key)
    )
    assert started.status_code == 201 and started.json()["status"] == "waiting_approval"
    rid = started.json()["run_id"]
    assert c.get(f"/v1/agents/ops-insights/runs/{rid}", headers=user(private_key)).json()["run_id"] == rid
    assert c.post(f"/v1/agents/ops-insights/runs/{rid}/resume", headers=user(private_key)).status_code == 200


def test_runs_are_private_to_their_owner(runtime_client, private_key):
    c = runtime_client
    rid = c.post("/v1/agents/ops-insights/runs", json={}, headers=user(private_key)).json()["run_id"]
    assert c.get(f"/v1/agents/ops-insights/runs/{rid}", headers=user(private_key, "mallory")).status_code == 404
    assert c.post(f"/v1/agents/ops-insights/runs/{rid}/resume", headers=user(private_key, "mallory")).status_code == 404


def test_runtime_rejects_bad_input_unknown_agents_and_missing_tokens(runtime_client, private_key):
    c = runtime_client
    assert (
        c.post("/v1/agents/ops-insights/runs", json={"input": {"days": 99}}, headers=user(private_key)).status_code
        == 422
    )
    assert c.post("/v1/agents/nope/runs", json={}, headers=user(private_key)).status_code == 404
    assert c.post("/v1/agents/ops-insights/runs", json={}).status_code == 401
    assert c.post("/v1/agents/ops-insights/runs", json={"other": 1}, headers=user(private_key)).status_code == 422
    wrong_audience = headers(private_key, "erin", audience="aurai-approvals")
    assert c.post("/v1/agents/ops-insights/runs", json={}, headers=wrong_audience).status_code == 401
