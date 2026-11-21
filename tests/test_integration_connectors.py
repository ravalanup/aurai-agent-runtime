"""The agent runtime against the real connector servers from aurai-mcp-connectors: the same wire format, the same
approval tokens, the same policy. Skipped unless that package and the opa binary are installed."""

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("aurai_connectors")
if not shutil.which("opa"):
    pytest.skip("opa is not installed", allow_module_level=True)

import aurai_connectors  # noqa: E402
import jwt  # noqa: E402
import uvicorn  # noqa: E402
from aurai_connectors.identity import IdentityResolver  # noqa: E402
from aurai_connectors.identity import TokenVerifier as ConnectorVerifier  # noqa: E402
from aurai_connectors.settings import Settings as ConnectorSettings  # noqa: E402
from aurai_connectors.wiring import build_server, http_app  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from aurai_agents.approval_service import ApprovalService  # noqa: E402
from aurai_agents.approval_store import MemoryApprovalStore  # noqa: E402
from aurai_agents.approval_tokens import mint  # noqa: E402
from aurai_agents.connector_client import ConnectorClient  # noqa: E402
from aurai_agents.errors import ApprovalNeeded, ConnectorRefused  # noqa: E402
from aurai_agents.runtime import Runtime  # noqa: E402
from aurai_agents.tokens import StaticTokenProvider  # noqa: E402
from tests.conftest import ALICE, APPROVER_GROUPS, ISSUER, LocalApprovals  # noqa: E402
from tests.conftest import SIGNING_KEY as KEY  # noqa: E402

REPO = Path(aurai_connectors.__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def connector_urls(private_key):
    settings = ConnectorSettings(
        connectors_dir=REPO / "connectors",
        policy_dir=REPO / "policy",
        issuer=ISSUER,
        audience="aurai-connectors",
        jwks_url="",
        opa_url="",
        approval_signing_key=KEY,
        redis_url="",
        otlp_endpoint="",
    )
    verifier = ConnectorVerifier(ISSUER, "aurai-connectors", key_resolver=lambda _t: private_key.public_key())
    urls, servers = {}, []
    for name in ("tickets", "ops-data", "messaging"):
        server = build_server(name, settings, identity=IdentityResolver(verifier))
        port = free_port()
        uv = uvicorn.Server(uvicorn.Config(http_app(server, settings, verifier), port=port, log_level="error"))
        threading.Thread(target=uv.run, daemon=True).start()
        servers.append(uv)
        urls[name] = f"http://127.0.0.1:{port}/mcp"
    for _ in range(100):
        if all(s.started for s in servers):
            break
        time.sleep(0.05)
    yield urls
    for s in servers:
        s.should_exit = True


def service_token(private_key, **over):
    claims = {
        "iss": ISSUER,
        "aud": "aurai-connectors",
        "sub": "svc-ops",
        "team_id": "ops-insights",
        "use_case_id": "ops-insights",
        "exp": int(time.time()) + 300,
        **over,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


def client(urls, private_key):
    return ConnectorClient(urls, StaticTokenProvider(service_token(private_key)))


def approval_for(arguments):
    return mint(
        KEY,
        tool="tickets_create_draft",
        arguments=arguments,
        team="ops-insights",
        use_case="ops-insights",
        approvers=["alice"],
    )


ARGS = {"title": "Inspect brake sensor", "body": "Alarm recurred twice.", "area": "Harbor Coaster"}


def test_a_read_works_and_a_write_without_approval_reports_what_is_needed(connector_urls, private_key):
    c = client(connector_urls, private_key)
    assert c.call("ops-data", "ops_downtime_summary", {"area": "Harbor Coaster", "days": 7}) is not None
    with pytest.raises(ApprovalNeeded) as e:
        c.call("tickets", "tickets_create_draft", ARGS)
    assert e.value.approvals_required == 1 and e.value.tool == "tickets_create_draft"


def test_our_args_hash_is_the_one_the_connector_asks_for(connector_urls, private_key):
    from aurai_agents.approval_tokens import args_hash

    with pytest.raises(ApprovalNeeded) as e:
        client(connector_urls, private_key).call("tickets", "tickets_create_draft", ARGS)
    assert e.value.args_hash == args_hash(ARGS)


def test_a_minted_token_is_accepted_once_and_only_for_those_arguments(connector_urls, private_key):
    c = client(connector_urls, private_key)
    token = mint(
        KEY,
        tool="tickets_create_draft",
        arguments=ARGS,
        team="ops-insights",
        use_case="ops-insights",
        approvers=["alice"],
    )
    done = c.call("tickets", "tickets_create_draft", ARGS, approval=token)
    assert done
    with pytest.raises(ConnectorRefused) as replay:
        c.call("tickets", "tickets_create_draft", ARGS, approval=token)
    assert replay.value.code == "approval_invalid"
    other = mint(
        KEY,
        tool="tickets_create_draft",
        arguments=ARGS,
        team="ops-insights",
        use_case="ops-insights",
        approvers=["alice"],
    )
    with pytest.raises(ConnectorRefused) as changed:
        c.call("tickets", "tickets_create_draft", {**ARGS, "title": "Something else"}, approval=other)
    assert changed.value.code == "approval_invalid"


def test_a_wrong_audience_token_is_refused(connector_urls, private_key):
    bad = ConnectorClient(connector_urls, StaticTokenProvider(service_token(private_key, aud="aurai-agents")))
    with pytest.raises(ConnectorRefused) as e:
        bad.call("tickets", "tickets_get", {"ticket_id": "TCK-1"})
    assert e.value.code == "unauthenticated"


def test_the_whole_agent_run_against_real_connectors(connector_urls, private_key, make_ctx):
    # real time, not the fake test clock: the connector checks the token's expiry against the wall clock
    service = ApprovalService(MemoryApprovalStore(), KEY, APPROVER_GROUPS)
    ctx = make_ctx(connectors=client(connector_urls, private_key), approvals=LocalApprovals(service))
    runtime = Runtime({"ops-insights": ctx}, InMemorySaver())
    view = runtime.start("ops-insights", {"areas": ["Harbor Coaster"]}, "erin")
    assert view["status"] == "waiting_approval"
    service.decide(ALICE, view["approvals"][0]["id"], "approve")
    done = runtime.resume("ops-insights", view["run_id"], "erin")
    assert done["status"] == "completed"
    assert [a["outcome"] for a in done["result"]["actions"]] == ["executed"], done["result"]["actions"]
