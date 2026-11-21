import os
from pathlib import Path

import pytest

from aurai_agents.cli import main
from aurai_agents.config import ConfigError
from aurai_agents.settings import Settings
from aurai_agents.wiring import build_approval_service, build_runtime, load_config

ROOT = Path(__file__).resolve().parents[1]
ENV = {
    "AURAI_CONFIG_DIR": str(ROOT / "config"),
    "AURAI_APPROVAL_SIGNING_KEY": "k" * 40,
    "AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET": "secret",
    "AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY": "sk-test",
}


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AURAI_DATABASE_URL", raising=False)


def test_the_shipped_config_is_consistent(env):
    agents, approvers = load_config(Settings.from_env())
    assert list(agents) == ["ops-insights"] and approvers["ops-insights"] == ["approvers-ops"]


def test_check_passes_on_the_shipped_config(env, capsys):
    assert main(["check"]) == 0
    assert "ok: 1 agent(s)" in capsys.readouterr().out


def test_check_fails_on_a_short_signing_key(env, monkeypatch, capsys):
    monkeypatch.setenv("AURAI_APPROVAL_SIGNING_KEY", "short")
    assert main(["check"]) == 1
    assert "at least 32" in capsys.readouterr().err


def test_check_fails_when_a_team_has_no_approvers(env, tmp_path, monkeypatch, capsys):
    (tmp_path / "agents.yaml").write_text((ROOT / "config/agents.yaml").read_text())
    (tmp_path / "approvers.yaml").write_text("teams:\n  finance-reporting: [approvers-finance]\n")
    monkeypatch.setenv("AURAI_CONFIG_DIR", str(tmp_path))
    assert main(["check"]) == 1
    assert "no approver groups" in capsys.readouterr().err


def test_an_agent_without_an_implementation_is_refused(env, tmp_path):
    text = (ROOT / "config/agents.yaml").read_text().replace("ops-insights:", "unbuilt:", 1)
    (tmp_path / "agents.yaml").write_text(text)
    (tmp_path / "approvers.yaml").write_text("teams:\n  ops-insights: [approvers-ops]\n")
    with pytest.raises(ConfigError, match="no implementation"):
        load_config(Settings.from_env({**os.environ, "AURAI_CONFIG_DIR": str(tmp_path)}))


def test_the_runtime_builds_from_config_and_the_environment(env):
    runtime, close = build_runtime(Settings.from_env())
    try:
        assert runtime.names() == ["ops-insights"]
    finally:
        close()


def test_a_missing_agent_secret_stops_startup(env, monkeypatch):
    monkeypatch.delenv("AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET")
    with pytest.raises(ConfigError, match="CLIENT_SECRET"):
        build_runtime(Settings.from_env())


def test_the_approval_service_builds_with_approver_groups_from_config(env):
    service = build_approval_service(Settings.from_env())
    assert service is not None


def test_init_db_needs_a_database(env, capsys):
    assert main(["init-db"]) == 1
    assert "AURAI_DATABASE_URL" in capsys.readouterr().err
