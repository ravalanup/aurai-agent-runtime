"""`aurai-agents new`: writes a working governed agent from the template. The generated test runs on its own."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

NAME = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
TEMPLATE_FILES = {
    "agent.py.tmpl": "src/aurai_agents/agents/{module}.py",
    "test_agent.py.tmpl": "tests/test_agent_{module}.py",
    "agent.yaml.tmpl": "config/agent-{name}.snippet.yaml",
}
NEXT_STEPS = """Created. Next:
  1. Register the agent in src/aurai_agents/runtime.py:
       BUILDERS["{name}"] = {module}.build
       INITIAL_STATE["{name}"] = {module}.initial_state
  2. Merge config/agent-{name}.snippet.yaml into config/agents.yaml and add the team to config/approvers.yaml.
  3. Replace the parts marked CHANGE ME, then run: python -m pytest tests/test_agent_{module}.py
  4. aurai-agents keycloak-plan   (shows the service client the agent needs)"""


class ScaffoldError(Exception):
    pass


def scaffold(directory: Path, name: str, team: str, use_case: str, tier: str) -> list[Path]:
    for label, value in (("name", name), ("team", team), ("use case", use_case)):
        if not NAME.fullmatch(value):
            raise ScaffoldError(f"{label} must be lowercase words joined by hyphens, got {value!r}")
    module = name.replace("-", "_")
    values = {
        "{{NAME}}": name,
        "{{MODULE}}": module,
        "{{TEAM}}": team,
        "{{USE_CASE}}": use_case,
        "{{TIER}}": tier,
        "{{ENV}}": "AURAI_AGENT_" + name.upper().replace("-", "_"),
    }
    targets = {src: directory / dest.format(module=module, name=name) for src, dest in TEMPLATE_FILES.items()}
    existing = [str(p) for p in targets.values() if p.exists()]
    if existing:
        raise ScaffoldError(f"refusing to overwrite {', '.join(existing)}")
    root = resources.files("aurai_agents") / "template"
    created = []
    for src, target in targets.items():
        text = (root / src).read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace(key, value)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        created.append(target)
    print(NEXT_STEPS.format(name=name, module=module))
    return created
