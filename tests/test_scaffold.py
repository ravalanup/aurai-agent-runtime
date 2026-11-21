import subprocess
import sys

import pytest
import yaml

from aurai_agents.cli import main
from aurai_agents.scaffold import ScaffoldError, scaffold


def test_the_generated_agent_passes_its_own_tests(tmp_path):
    """Scaffold a module and its test into a scratch project, make the module importable the way a real checkout
    does (it lives in the agents package), and run the generated tests in a fresh interpreter."""
    created = scaffold(tmp_path, "ticket-triage", "internal-help", "ticket-triage", "T2")
    assert {p.name for p in created} == {
        "ticket_triage.py",
        "test_agent_ticket_triage.py",
        "agent-ticket-triage.snippet.yaml",
    }
    (tmp_path / "tests" / "conftest.py").write_text(
        f"import aurai_agents.agents as pkg\npkg.__path__.append({str(tmp_path / 'src/aurai_agents/agents')!r})\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 passed" in result.stdout


def test_placeholders_are_all_replaced_and_the_snippet_is_valid_config(tmp_path):
    scaffold(tmp_path, "gate-watch", "ops-insights", "gate-watch", "T1")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "{{" not in path.read_text(), path
    lines = (tmp_path / "config/agent-gate-watch.snippet.yaml").read_text().splitlines()
    body = [line for line in lines if not line.lstrip().startswith("#")]
    spec = yaml.safe_load("agents:\n" + "\n".join(body))
    assert spec["agents"]["gate-watch"]["client_secret_env"] == "AURAI_AGENT_GATE_WATCH_CLIENT_SECRET"


@pytest.mark.parametrize("bad", ["Bad Name", "../escape", "under_score", "", "-lead"])
def test_unsafe_names_are_refused(tmp_path, bad):
    with pytest.raises(ScaffoldError):
        scaffold(tmp_path, bad, "ops-insights", "x", "T1")
    assert not list(tmp_path.iterdir())


def test_existing_files_are_never_overwritten(tmp_path):
    scaffold(tmp_path, "gate-watch", "ops-insights", "gate-watch", "T1")
    with pytest.raises(ScaffoldError, match="refusing to overwrite"):
        scaffold(tmp_path, "gate-watch", "ops-insights", "gate-watch", "T1")


def test_the_cli_reports_errors_without_a_traceback(tmp_path, capsys):
    assert main(["new", "Bad Name", "--team", "t", "--use-case", "u", "--directory", str(tmp_path)]) == 1
    assert "cannot scaffold" in capsys.readouterr().err
