import json

import httpx
import pytest

from aurai_agents.keycloak_clients import (
    APPROVALS_AUDIENCE,
    CONNECTORS_AUDIENCE,
    KeycloakAdmin,
    agent_client,
    apply_plan,
    desired_clients,
    make_plan,
)
from tests.conftest import AGENT


def admin_for(handler):
    return KeycloakAdmin(
        "http://kc", "aurai", "admin", "pw", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def claims(spec):
    return {m["name"]: m["config"]["claim.value"] for m in spec.mappers if "claim.value" in m["config"]}


def audiences(spec):
    return {m["config"]["included.custom.audience"] for m in spec.mappers if "included.custom.audience" in m["config"]}


def test_an_agent_client_is_a_service_account_with_fixed_team_and_use_case_claims():
    spec = agent_client(AGENT)
    f = spec.fields
    assert f["serviceAccountsEnabled"] and not f["standardFlowEnabled"] and not f["directAccessGrantsEnabled"]
    assert not f["publicClient"]
    assert claims(spec) == {"team_id": AGENT.team_id, "use_case_id": AGENT.use_case_id}
    assert audiences(spec) == {CONNECTORS_AUDIENCE, APPROVALS_AUDIENCE}


def test_the_console_client_is_public_with_pkce_groups_and_the_service_audiences():
    console = next(s for s in desired_clients([AGENT]) if s.client_id == "agents-console")
    assert console.fields["publicClient"] and console.fields["attributes"]["pkce.code.challenge.method"] == "S256"
    assert audiences(console) == {"aurai-agents", APPROVALS_AUDIENCE}
    assert any(m["config"].get("claim.name") == "groups" for m in console.mappers)
    assert "secret" not in console.representation()


def existing_from(spec, **over):
    body = {"id": "kc-1", **spec.representation(), **over}
    return {**body, "protocolMappers": [{**m, "id": "m"} for m in spec.mappers]}


def test_a_missing_client_is_planned_for_creation_and_a_matching_one_is_left_alone():
    plan = make_plan(desired_clients([AGENT]), [])
    assert plan.describe()["create"] == ["agents-console", "n8n-orchestrator", "agent-ops-insights"]
    in_sync = [existing_from(s) for s in desired_clients([AGENT])]
    again = make_plan(desired_clients([AGENT]), in_sync)
    assert not again.create and not again.update and len(again.unchanged) == 3


def test_drift_is_detected_in_fields_and_in_claim_mappers():
    spec = agent_client(AGENT)
    drifted = existing_from(spec, directAccessGrantsEnabled=True)
    wrong = [
        {**m, "config": {**m["config"], "claim.value": "finance-reporting"}} if m["name"] == "team_id" else m
        for m in drifted["protocolMappers"]
    ]
    drifted["protocolMappers"] = wrong
    plan = make_plan([spec], [drifted])
    assert plan.describe()["update"] == {"agent-ops-insights": ["directAccessGrantsEnabled", "mapper:team_id"]}


def test_extra_keycloak_defaults_in_mapper_config_are_not_drift():
    spec = agent_client(AGENT)
    current = existing_from(spec)
    current["protocolMappers"] = [
        {**m, "config": {**m["config"], "introspection.token.claim": "true"}} for m in current["protocolMappers"]
    ]
    assert make_plan([spec], [current]).update == []


def test_apply_creates_with_the_secret_from_the_environment_and_never_prints_it():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.read()))
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "admin"})
        return httpx.Response(201)

    admin = KeycloakAdmin(
        "http://kc", "aurai", "admin", "pw", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    plan = make_plan([agent_client(AGENT)], [])
    assert "s3cret" not in json.dumps(plan.describe())
    apply_plan(admin, plan, {AGENT.client_secret_env: "s3cret"})
    created = json.loads(calls[-1][2])
    assert created["secret"] == "s3cret" and calls[-1][1] == "/admin/realms/aurai/clients"


def test_creating_without_a_secret_in_the_environment_is_refused():
    plan = make_plan([agent_client(AGENT)], [])
    with pytest.raises(ValueError, match=AGENT.client_secret_env):
        apply_plan(None, plan, {})


def test_apply_updates_in_place_and_keeps_unrelated_mappers():
    spec = agent_client(AGENT)
    current = existing_from(spec, directAccessGrantsEnabled=True)
    current["protocolMappers"].append(
        {"id": "x", "name": "custom", "protocolMapper": "oidc-usermodel-property-mapper", "config": {}}
    )
    sent = {}

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "admin"})
        sent["path"], sent["body"] = request.url.path, json.loads(request.read())
        return httpx.Response(204)

    admin = admin_for(handler)
    apply_plan(admin, make_plan([spec], [current]), {})
    assert sent["path"] == "/admin/realms/aurai/clients/kc-1"
    assert sent["body"]["directAccessGrantsEnabled"] is False
    assert {m["name"] for m in sent["body"]["protocolMappers"]} >= {"custom", "team_id", "use_case_id"}


def test_the_workflow_client_can_only_reach_the_runtime():
    orchestrator = next(s for s in desired_clients([AGENT]) if s.client_id == "n8n-orchestrator")
    assert audiences(orchestrator) == {"aurai-agents"}
    assert claims(orchestrator) == {}  # no team or use case: connectors would refuse it anyway
    assert orchestrator.fields["serviceAccountsEnabled"] and not orchestrator.fields["publicClient"]
