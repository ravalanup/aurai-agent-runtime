import pytest

pytest.importorskip("claude_agent_sdk")

from aurai_agents.reference.claude_agent_sdk_agent import ToolGuard, build_options, execute_approved  # noqa: E402
from tests.conftest import AGENT, ALICE, FakeConnectors, LocalApprovals  # noqa: E402


@pytest.fixture
def guard(service):
    return ToolGuard(AGENT, LocalApprovals(service), "run-1", "erin")


async def ask(guard, name, args=None):
    return await guard.can_use_tool(name, args or {}, None)


async def test_reads_on_the_agents_connectors_are_allowed(guard):
    assert (await ask(guard, "mcp__ops-data__ops_incidents", {"area": "Harbor Coaster"})).behavior == "allow"
    assert (await ask(guard, "mcp__tickets__tickets_search", {"query": "x"})).behavior == "allow"


async def test_built_in_tools_and_unknown_connectors_are_denied(guard):
    for name in ("Bash", "Write", "WebFetch", "mcp__files__files_read", "mcp__tickets", "mcp__x__y__z__w"):
        assert (await ask(guard, name)).behavior == "deny", name
    assert guard.requested == []


async def test_a_write_is_never_run_and_files_one_approval(guard, service):
    args = {"title": "Check sensor", "body": "Recurring", "area": "Harbor Coaster"}
    first = await ask(guard, "mcp__tickets__tickets_create_draft", args)
    again = await ask(guard, "mcp__tickets__tickets_create_draft", args)  # the model retries
    assert first.behavior == again.behavior == "deny" and "approval" in first.message
    assert len(guard.requested) == 1
    assert (
        service.view(service.get_for_agent(guard.approvals.caller, guard.requested[0].approval_id))["status"]
        == "pending"
    )


async def test_a_write_tool_on_the_wrong_connector_is_denied(guard):
    assert (await ask(guard, "mcp__messaging__tickets_create_draft", {})).behavior == "deny"


async def test_approved_requests_run_once_and_others_are_reported(guard, service):
    args = {"title": "Check sensor", "body": "Recurring", "area": "Harbor Coaster"}
    await ask(guard, "mcp__tickets__tickets_create_draft", args)
    connectors = FakeConnectors()
    assert execute_approved(guard, connectors, guard.approvals) == [
        {"tool": "tickets_create_draft", "outcome": "pending"}
    ]
    service.decide(ALICE, guard.requested[0].approval_id, "approve")
    assert execute_approved(guard, connectors, guard.approvals) == [
        {"tool": "tickets_create_draft", "outcome": "executed"}
    ]
    assert len(connectors.created) == 1
    assert execute_approved(guard, connectors, guard.approvals)[0]["outcome"] == "executed"
    assert len(connectors.created) == 1  # calling it again must not create a second ticket


def test_the_sdk_options_offer_no_built_in_tools_and_route_everything_through_the_guard(guard):
    options = build_options(AGENT, "svc-token", guard)
    assert options.tools == [] and options.allowed_tools == [] and options.can_use_tool == guard.can_use_tool
    assert options.mcp_servers["tickets"]["headers"] == {"Authorization": "Bearer svc-token"}
    assert set(options.mcp_servers) == set(AGENT.connectors)
