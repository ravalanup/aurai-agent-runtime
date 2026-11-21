# Frameworks

ADR 0003 chose LangGraph as the primary framework, n8n for business builders, and the Claude Agent SDK and CrewAI as reference implementations. This repository builds the first three; CrewAI was not chosen for this milestone.

| Framework | Role here | Where | Model chooses tools? |
|---|---|---|---|
| LangGraph | Primary. Durable state, the approval pause, the template. | `agents/`, `kit.py` | No: it returns data |
| n8n | Low-code orchestration of runs | `n8n/` | n/a |
| Claude Agent SDK | Reference: the same controls where the model *does* pick tools | `reference/claude_agent_sdk_agent.py` | Yes, through the permission callback |

The span attribute `aurai.agent.framework` takes the values `langgraph`, `n8n`, `claude-agent-sdk` and `crewai`, so traces from different frameworks are comparable.

## Claude Agent SDK reference

The SDK runs its own agent loop, so the control point is `can_use_tool`, called before every tool call:

1. Built-in tools are not offered (`tools=[]`) and nothing is pre-approved (`allowed_tools=[]`).
2. Only the read tools in `READS` on the agent's own connectors are allowed.
3. A write tool in `WRITES` is **not run**. The callback files an approval request for exactly that call and tells the model a request was filed and not to retry.
4. Everything else is denied.

After the loop, `execute_approved` makes the approved calls itself, once each, with the signed token. The connectors verify the approval again, so a bug in the callback still cannot produce a write without a person's approval.

Tested without the SDK or a model: reads allowed, built-ins and other connectors denied, one request per distinct write (a model that retries does not file more), a write on the wrong connector denied, approved requests run exactly once, and the options object offers no built-in tools. **Not verified:** a run against a live model, which needs an Anthropic API key.

## What a different framework must do

Whatever the framework, an agent here must: run as its own service client; call connectors only with that identity; route every write through an approval request and the signed token; keep prompts and results out of telemetry; and emit `aurai.agent.step` and `aurai.approval` spans with the common attributes.
