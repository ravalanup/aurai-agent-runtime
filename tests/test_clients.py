import json

import httpx
import pytest

from aurai_agents.approval_client import HttpApprovalClient
from aurai_agents.connector_client import ConnectorClient
from aurai_agents.errors import ApprovalError, ApprovalNeeded, ConnectorRefused
from aurai_agents.model import GatewayModel
from aurai_agents.tokens import ClientCredentialsProvider, StaticTokenProvider


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def rpc_result(result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def test_client_credentials_are_cached_until_near_expiry():
    calls, now = [], [1000.0]

    def handler(request):
        calls.append(request.read().decode())
        return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_in": 300})

    p = ClientCredentialsProvider("http://kc/token", "agent-x", "s3cret", client=mock(handler), clock=lambda: now[0])
    assert p.token() == "tok1" and p.token() == "tok1" and len(calls) == 1
    assert "grant_type=client_credentials" in calls[0] and "client_id=agent-x" in calls[0]
    now[0] += 280  # inside the 30 second skew
    assert p.token() == "tok2"


def test_a_token_endpoint_failure_is_raised():
    p = ClientCredentialsProvider("http://kc/token", "a", "b", client=mock(lambda r: httpx.Response(401)))
    with pytest.raises(httpx.HTTPStatusError):
        p.token()


def connector(handler):
    return ConnectorClient({"tickets": "http://t/mcp"}, StaticTokenProvider("svc"), client=mock(handler))


def test_a_call_sends_the_service_token_and_the_approval_in_headers_not_arguments():
    seen = {}

    def handler(request):
        seen["headers"], seen["body"] = request.headers, json.loads(request.read())
        return rpc_result(
            {"content": [{"type": "text", "text": '{"id": "TCK-1"}'}], "structuredContent": {"id": "TCK-1"}}
        )

    out = connector(handler).call("tickets", "tickets_get", {"ticket_id": "TCK-1"}, approval="signed")
    assert out == {"id": "TCK-1"}
    assert seen["headers"]["authorization"] == "Bearer svc" and seen["headers"]["x-aurai-approval"] == "signed"
    assert seen["body"]["params"] == {"name": "tickets_get", "arguments": {"ticket_id": "TCK-1"}}


def test_list_results_come_from_structured_content():
    r = rpc_result({"content": [], "structuredContent": {"result": [{"id": 1}, {"id": 2}]}})
    assert connector(lambda req: r).call("tickets", "tickets_search", {}) == [{"id": 1}, {"id": 2}]


def test_an_approval_requirement_becomes_approval_needed():
    text = (
        'Error executing tool tickets_create_draft: {"approvals_required": 2, "args_hash": "abc", '
        '"error": "approval_required", "message": "2 approval(s) required", "tool": "tickets_create_draft"}'
    )
    r = rpc_result({"isError": True, "content": [{"type": "text", "text": text}]})
    with pytest.raises(ApprovalNeeded) as e:
        connector(lambda req: r).call("tickets", "tickets_create_draft", {})
    assert e.value.approvals_required == 2 and e.value.args_hash == "abc"


def test_other_refusals_carry_the_connectors_code():
    text = 'Error executing tool x: {"error": "policy_denied", "message": "not allowed"}'
    with pytest.raises(ConnectorRefused) as e:
        connector(lambda req: rpc_result({"isError": True, "content": [{"type": "text", "text": text}]})).call(
            "tickets", "x", {}
        )
    assert e.value.code == "policy_denied"
    with pytest.raises(ConnectorRefused) as e:
        connector(lambda req: rpc_result({"isError": True, "content": [{"type": "text", "text": "garbled"}]})).call(
            "tickets", "x", {}
        )
    assert e.value.code == "tool_error"


def test_http_401_is_an_auth_refusal_and_unknown_connectors_are_refused():
    with pytest.raises(ConnectorRefused) as e:
        connector(lambda req: httpx.Response(401)).call("tickets", "x", {})
    assert e.value.code == "unauthenticated"
    with pytest.raises(ConnectorRefused) as e:
        connector(lambda req: httpx.Response(200)).call("nope", "x", {})
    assert e.value.code == "unknown_connector"


def test_protocol_errors_are_refusals():
    r = httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad params"}})
    with pytest.raises(ConnectorRefused) as e:
        connector(lambda req: r).call("tickets", "x", {})
    assert e.value.code == "protocol_error"


def test_approval_client_maps_http_errors_to_approval_errors():
    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(409, json={"detail": "the approval is pending, not approved"})
        return httpx.Response(201, json={"id": "a1", "status": "pending"})

    c = HttpApprovalClient("http://approvals", StaticTokenProvider("svc"), client=mock(handler))
    assert c.create(x=1)["id"] == "a1"
    with pytest.raises(ApprovalError) as e:
        c.token("a1")
    assert e.value.status_code == 409 and "pending" in e.value.message


def test_gateway_model_posts_an_openai_style_request():
    seen = {}

    def handler(request):
        seen["auth"], seen["body"] = request.headers["authorization"], json.loads(request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    m = GatewayModel("http://gw:4000/", "sk-key", "aurai-balanced", client=mock(handler))
    assert m.complete("sys", "usr") == "hello"
    assert seen["auth"] == "Bearer sk-key" and seen["body"]["model"] == "aurai-balanced"
    assert [x["role"] for x in seen["body"]["messages"]] == ["system", "user"]
