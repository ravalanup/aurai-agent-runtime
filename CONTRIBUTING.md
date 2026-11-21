# Contributing

## Set up

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Optional, to run more of the tests: a Postgres (`AURAI_TEST_POSTGRES_URL`), `pip install -e ../aurai-mcp-connectors` with the `opa` binary, and `pip install -e ../aurai-eval-governance`.

## Before you open a pull request

```powershell
ruff check .
ruff format --check .
pytest -q
$env:AURAI_APPROVAL_SIGNING_KEY = "local-only-signing-key-0123456789abcdef"; aurai-agents check
```

CI runs the same with a Postgres service, plus `helm lint`, `helm template` with kubeconform, a container build and secret scanning.

## Rules that protect the approval path

- **A write never runs from a node.** Put it in `proposals` and let `ApprovalGate` take it through approval. Do not call a write tool directly, and do not pass a token you did not collect from the approval service.
- **The model returns data.** Parse its reply as untrusted text and build proposals in code. Never let it name a tool, a connector or an approver.
- **Identity comes from the verified token.** Team, use case and groups are never read from a body, header or argument.
- **Keep decisions with people.** Nothing in this repository may approve on someone's behalf, widen who can approve, or let a request be decided by whoever made it. Changes to `config/approvers.yaml` need a reviewer from the owner of the team's data.
- **Nodes must be safe to run twice.** LangGraph re-enters an interrupted node on resume. Use idempotency keys and record what has been done.
- **Do not log or trace prompts, arguments or results.** Spans carry identifiers and outcomes only, and a test asserts it.
- **Fail closed.** An unexpected answer from the approval service or a connector is a failed action, never a retry that could run twice.
- Keep sample data synthetic. No real names, customer data or internal documents.

## Commit messages

Imperative mood, a short summary line, and a body that says why when it is not obvious.
