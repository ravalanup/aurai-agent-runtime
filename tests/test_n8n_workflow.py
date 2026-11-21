import json
import re
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "n8n" / "ops-insights-weekday-briefing.json"


@pytest.fixture(scope="module")
def workflow():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_node_names_are_unique_and_every_connection_points_at_a_node(workflow):
    names = [n["name"] for n in workflow["nodes"]]
    assert len(names) == len(set(names))
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for target in branch:
                assert target["node"] in names


def test_it_is_a_loop_from_the_check_through_wait_and_resume_back_to_the_check(workflow):
    c = workflow["connections"]
    assert c["Waiting for approval?"]["main"][0][0]["node"] == "Wait 15 minutes"
    assert c["Wait 15 minutes"]["main"][0][0]["node"] == "Resume run"
    assert c["Resume run"]["main"][0][0]["node"] == "Waiting for approval?"
    assert c["Waiting for approval?"]["main"][1][0]["node"] == "Briefing"  # the other branch ends the run


def test_it_talks_only_to_the_runtime_api_and_holds_no_secrets(workflow):
    text = PATH.read_text(encoding="utf-8")
    urls = [n["parameters"]["url"] for n in workflow["nodes"] if "url" in n["parameters"]]
    assert urls and all(u.startswith("={{ $env.AURAI_RUNTIME_URL }}/v1/agents/") for u in urls)
    assert not re.search(r"svc\.cluster\.local|connector-|/mcp|approvals", text)  # never the connectors or approvals
    assert not re.search(r"(?i)bearer\s+[a-z0-9]|client_secret|password|api[_-]?key", text)
    for node in workflow["nodes"]:
        if node["type"].endswith("httpRequest"):
            assert node["parameters"]["authentication"] == "predefinedCredentialType"  # token from a credential


def test_it_ships_inactive(workflow):
    assert workflow["active"] is False
