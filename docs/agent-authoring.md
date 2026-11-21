# Writing an agent

Most agents are the same shape: read some facts, ask a model for a judgement, and propose a small number of actions that people approve. The template builds that shape so a new agent starts with the controls already in place.

```powershell
aurai-agents new ticket-triage --team internal-help --use-case ticket-triage --tier T2
```

It writes three files and prints what to do next:

| File | Purpose |
|---|---|
| `src/aurai_agents/agents/ticket_triage.py` | The graph: `gather`, `decide`, the approval gate nodes, `summarize`. Parts to change are marked `CHANGE ME`. |
| `tests/test_agent_ticket_triage.py` | Runs the real graph with a scripted model and connectors that enforce the real approval protocol. |
| `config/agent-ticket-triage.snippet.yaml` | The entry for `agents.yaml`. |

The generated tests pass as generated (`tests/test_scaffold.py` checks this), so a change that breaks the pattern shows up straight away.

## Steps

1. **Scaffold** as above. Names are lowercase words joined by hyphens; the command refuses to overwrite existing files.
2. **Register** the agent in `runtime.py`: one line each for `BUILDERS` and `INITIAL_STATE`.
3. **Configure**: merge the snippet into `config/agents.yaml`, add the team's approver groups to `config/approvers.yaml`. `aurai-agents check` reports a missing approver group, a duplicate client or use case, and an agent with no implementation.
4. **Identity**: `aurai-agents keycloak-plan` shows the service client the agent needs.
5. **Governance**: the use case must exist in aurai-eval-governance (its risk tier decides the approvals connectors demand) and the team must exist in the gateway and in the connectors' `policy/teams.yaml`.
6. **Change the `CHANGE ME` parts**, keeping the rules below.

## Rules the template keeps

- **The model returns data.** Parse its reply as untrusted text and build proposals in code. Never let it name a tool or a connector.
- **Validate input and output.** `initial_state` rejects bad input with a message the caller can act on; `parse_model_output` drops what is malformed and counts it.
- **Reads can fail.** Catch `ConnectorRefused` in `gather`, record it, and carry on. A denied read is information, not a crash.
- **Writes go through the gate.** Put them in `proposals`; do not call a write tool from a node. The gate probes, files the request, pauses, and executes only what was approved.
- **Nodes must be safe to repeat.** After a restart LangGraph re-enters the interrupted node. Use `ctx.step(...)` for spans and the run id for idempotency keys, as the gate does.
- **Do not put prompts, arguments or results in spans or logs.** `tracing.py` attaches only identifiers and outcomes.

## Testing helpers

`aurai_agents.testing` provides `ScriptedModel`, `ScriptedConnectors` (which refuses a protected tool until it is given a token signed for exactly those arguments, and accepts each token once) and `LocalApprovals` (the approval client backed by an in-process service). They let a test cover the pause, an approval, a rejection and a duplicate resume without a cluster.

## What the template does not cover

An agent that needs several different write tools, several approvers, a branch that depends on an approval's outcome, or long-running work between steps. The gate takes a list of proposals of any connector and tool, so the first two fit; the others mean extending the graph, not the gate.
