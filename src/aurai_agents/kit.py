"""Building blocks shared by every governed LangGraph agent, so a new agent gets the same approval behaviour.

`AgentContext` holds what an agent needs (its identity, connectors, approval service, model, tracer).
`ApprovalGate` supplies three graph nodes that carry a list of proposed actions through approval:

    open     try each proposal. A connector that wants approval answers with the exact requirement; the gate files an
             approval request for that call. Calls that need none run now.
    wait     look at the requests; if any is still pending, pause the graph with an interrupt. The checkpoint is saved,
             so the process can stop and the run can resume later, on another replica.
    execute  for each approved request collect its signed token and make the call once. Rejected and expired requests
             are recorded and not run.

A proposal is {"connector", "tool", "arguments", "summary"}. The gate never lets the model choose a tool: the graph
decides which tools a proposal may use before the gate ever sees it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt
from opentelemetry import trace

from . import tracing
from .approval_client import ApprovalsAPI
from .config import AgentConfig
from .connector_client import ConnectorClient
from .errors import ApprovalError, ApprovalNeeded, ConnectorRefused
from .model import ModelClient


@dataclass
class AgentContext:
    config: AgentConfig
    connectors: ConnectorClient
    approvals: ApprovalsAPI
    model: ModelClient
    tracer: trace.Tracer

    def step(self, name: str):
        return tracing.step(self.tracer, self.config, name)


class ApprovalGate:
    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def open(self, state: dict[str, Any]) -> dict[str, Any]:
        with self.ctx.step("open_approvals"):
            approvals: list[dict] = []
            results: list[dict] = list(state.get("results") or [])
            for index, proposal in enumerate(state.get("proposals") or []):
                try:
                    out = self.ctx.connectors.call(proposal["connector"], proposal["tool"], proposal["arguments"])
                    results.append(_result(index, proposal, "executed", result=out))
                except ApprovalNeeded as need:
                    record = self.ctx.approvals.create(
                        idempotency_key=f"{state['run_id']}:{index}",
                        connector=proposal["connector"],
                        tool=proposal["tool"],
                        arguments=proposal["arguments"],
                        team=self.ctx.config.team_id,
                        use_case=self.ctx.config.use_case_id,
                        summary=proposal["summary"][:500],
                        required=need.approvals_required,
                        on_behalf_of=state.get("owner"),
                    )
                    approvals.append({"index": index, "id": record["id"]})
                except ConnectorRefused as refused:
                    results.append(_result(index, proposal, "refused", code=refused.code))
            return {"approvals": approvals, "results": results}

    def _statuses(self, state: dict[str, Any]) -> dict[str, str]:
        return {a["id"]: self.ctx.approvals.get(a["id"])["status"] for a in state.get("approvals") or []}

    def wait(self, state: dict[str, Any]) -> dict[str, Any]:
        with self.ctx.step("wait_for_approval"):
            statuses = self._statuses(state)
            if "pending" in statuses.values():
                tracing.approval_span(self.ctx.tracer, self.ctx.config, "pending")
                # First pass: raises, and the graph pauses with its state checkpointed. On resume this node runs
                # again from the top, interrupt() returns the resume value, and the statuses are read afresh.
                interrupt({"waiting_for": [a["id"] for a in state["approvals"] if statuses[a["id"]] == "pending"]})
                statuses = self._statuses(state)
            return {"statuses": statuses}

    @staticmethod
    def route_after_wait(state: dict[str, Any]) -> str:
        return "wait" if "pending" in (state.get("statuses") or {}).values() else "execute"

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        with self.ctx.step("execute_approved"):
            results = list(state.get("results") or [])
            proposals = state.get("proposals") or []
            for item in state.get("approvals") or []:
                proposal = proposals[item["index"]]
                status = (state.get("statuses") or {}).get(item["id"], "pending")
                if status != "approved":
                    tracing.approval_span(self.ctx.tracer, self.ctx.config, "rejected")
                    results.append(_result(item["index"], proposal, status))  # rejected or expired
                    continue
                tracing.approval_span(self.ctx.tracer, self.ctx.config, "approved")
                try:
                    token = self.ctx.approvals.token(item["id"])
                    out = self.ctx.connectors.call(
                        proposal["connector"], proposal["tool"], proposal["arguments"], token
                    )
                    results.append(_result(item["index"], proposal, "executed", result=out))
                except (ConnectorRefused, ApprovalError) as err:
                    code = err.code if isinstance(err, ConnectorRefused) else f"approval_service_{err.status_code}"
                    results.append(_result(item["index"], proposal, "failed", code=code))
            return {"results": results}


def _result(index: int, proposal: dict, outcome: str, **extra: Any) -> dict[str, Any]:
    return {"index": index, "tool": proposal["tool"], "summary": proposal["summary"], "outcome": outcome, **extra}
