"""The runtime: starts runs of registered agents, reports their state and resumes them after approvals.

A run is one LangGraph thread. Its owner is the user who started it; only the owner can read or resume it."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from .agents import ops_insights
from .errors import ApprovalError
from .kit import AgentContext

log = logging.getLogger("aurai.runtime")

BUILDERS: dict[str, Callable[..., Any]] = {"ops-insights": ops_insights.build}
# What a run starts with, from the caller's input. Raises ValueError for input the agent does not accept.
INITIAL_STATE: dict[str, Callable[[dict], dict]] = {"ops-insights": ops_insights.initial_state}


class RunNotFound(Exception):
    pass


@dataclass
class Agent:
    ctx: AgentContext
    graph: Any


class Runtime:
    def __init__(self, contexts: dict[str, AgentContext], checkpointer) -> None:
        self._agents: dict[str, Agent] = {}
        for name, ctx in contexts.items():
            if name not in BUILDERS:
                raise KeyError(f"no agent implementation registered for {name!r}")
            self._agents[name] = Agent(ctx, BUILDERS[name](ctx, checkpointer))

    def names(self) -> list[str]:
        return sorted(self._agents)

    def _config(self, agent: str, run_id: str) -> dict:
        return {"configurable": {"thread_id": f"{agent}:{run_id}"}}

    def start(self, agent: str, payload: dict, owner: str) -> dict:
        if agent not in self._agents:
            raise KeyError(agent)
        initial = INITIAL_STATE[agent](payload)
        run_id = uuid.uuid4().hex
        state = {"run_id": run_id, "owner": owner, **initial}
        self._advance(agent, run_id, state)
        return self.view(agent, run_id, owner)

    def resume(self, agent: str, run_id: str, owner: str) -> dict:
        self._require(agent, run_id, owner)
        self._advance(agent, run_id, Command(resume=True))
        return self.view(agent, run_id, owner)

    def _advance(self, agent: str, run_id: str, command: Any) -> None:
        try:
            self._agents[agent].graph.invoke(command, self._config(agent, run_id))
        except Exception:  # the checkpoint keeps what was done; the view reports the run as failed
            log.exception("run %s of %s failed", run_id, agent)
            try:
                self._agents[agent].graph.update_state(self._config(agent, run_id), {"status": "failed"})
            except Exception:  # no checkpoint exists if the run failed before its first step
                log.exception("could not record the failure of run %s", run_id)

    def _require(self, agent: str, run_id: str, owner: str):
        if agent not in self._agents:
            raise RunNotFound(run_id)
        snapshot = self._agents[agent].graph.get_state(self._config(agent, run_id))
        if not snapshot.values or snapshot.values.get("owner") != owner:
            raise RunNotFound(run_id)  # a run that is not yours looks the same as one that does not exist
        return snapshot

    def view(self, agent: str, run_id: str, owner: str) -> dict:
        snapshot = self._require(agent, run_id, owner)
        values = snapshot.values
        if values.get("status") == "failed":
            status = "failed"
        elif snapshot.next:
            status = "waiting_approval"
        else:
            status = "completed"
        approvals = []
        for item in values.get("approvals") or []:
            try:
                approvals.append(
                    {"id": item["id"], "status": self._agents[agent].ctx.approvals.get(item["id"])["status"]}
                )
            except ApprovalError:
                approvals.append({"id": item["id"], "status": "unknown"})
        return {
            "run_id": run_id,
            "agent": agent,
            "status": status,
            "approvals": approvals,
            "result": {
                "briefing": values.get("briefing"),
                "proposals": len(values.get("proposals") or []),
                "dropped_proposals": values.get("dropped", 0),
                "actions": [
                    {k: r[k] for k in ("tool", "summary", "outcome", "code") if k in r}
                    for r in values.get("results") or []
                ],
            },
        }
