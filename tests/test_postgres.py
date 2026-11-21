"""State in a real Postgres: the LangGraph checkpointer and the approval store. Set AURAI_TEST_POSTGRES_URL to run
these (CI starts a Postgres service and sets it)."""

import os
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver  # noqa: F401

from aurai_agents.approval_service import ApprovalService
from aurai_agents.approval_store import PostgresApprovalStore
from aurai_agents.checkpoint import make_checkpointer
from aurai_agents.runtime import Runtime
from tests.conftest import ALICE, APPROVER_GROUPS, SERVICE, SIGNING_KEY, LocalApprovals, create_request

URL = os.environ.get("AURAI_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="AURAI_TEST_POSTGRES_URL is not set")


@pytest.fixture
def store():
    s = PostgresApprovalStore(URL)
    s.setup()
    yield s
    s.close()


@pytest.fixture
def pg_service(store, clock):
    return ApprovalService(store, SIGNING_KEY, APPROVER_GROUPS, clock=clock)


def unique(**kw):
    return create_request(key=f"k-{uuid.uuid4().hex}", **kw)


def test_the_approval_lifecycle_works_in_postgres(pg_service):
    aid = pg_service.create(SERVICE, unique()).id
    assert pg_service.status(pg_service.get_for_agent(SERVICE, aid)) == "pending"
    assert pg_service.status(pg_service.decide(ALICE, aid, "approve")) == "approved"
    assert pg_service.mint_token(SERVICE, aid)


def test_create_is_idempotent_on_the_key(pg_service):
    req = unique()
    first, second = pg_service.create(SERVICE, req), pg_service.create(SERVICE, req)
    assert first.id == second.id


def test_a_second_process_sees_the_same_approvals(store, pg_service, clock):
    aid = pg_service.create(SERVICE, unique()).id
    other = PostgresApprovalStore(URL)
    try:
        replica = ApprovalService(other, SIGNING_KEY, APPROVER_GROUPS, clock=clock)
        assert replica.get_for_agent(SERVICE, aid).id == aid
    finally:
        other.close()


def test_concurrent_decisions_cannot_both_win(store, pg_service):
    from concurrent.futures import ThreadPoolExecutor

    from aurai_agents.errors import ApprovalError
    from tests.conftest import CAROL

    aid = pg_service.create(SERVICE, unique(required=2)).id

    def decide(p):
        try:
            return pg_service.status(pg_service.decide(p, aid, "approve"))
        except ApprovalError:
            return "error"

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(decide, [ALICE, ALICE]))  # the same approver twice, at once
    assert len(pg_service.get_for_agent(SERVICE, aid).decisions) == 1
    assert CAROL


def test_a_waiting_run_resumes_after_the_process_restarts(make_ctx, pg_service):
    saver, close = make_checkpointer(URL)
    ctx = make_ctx(approvals=LocalApprovals(pg_service))
    view = Runtime({"ops-insights": ctx}, saver).start("ops-insights", {"areas": ["Harbor Coaster"]}, "erin")
    assert view["status"] == "waiting_approval"
    close()  # the process goes away; only Postgres remains
    pg_service.decide(ALICE, view["approvals"][0]["id"], "approve")
    saver2, close2 = make_checkpointer(URL)
    try:
        ctx2 = make_ctx(approvals=LocalApprovals(pg_service))
        done = Runtime({"ops-insights": ctx2}, saver2).resume("ops-insights", view["run_id"], "erin")
        assert done["status"] == "completed"
        assert [a["outcome"] for a in done["result"]["actions"]] == ["executed"]
    finally:
        close2()
