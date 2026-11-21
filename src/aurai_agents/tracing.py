"""Spans follow the Aurai conventions (aurai-eval-governance): `aurai.agent.step` names the agent and framework and
`aurai.approval` records whether approval was required and its outcome. Every span carries the use case, team and risk
tier. Prompts, tool arguments and results are never attached."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SPAN_AGENT_STEP = "aurai.agent.step"
SPAN_APPROVAL = "aurai.approval"


def setup_tracing(otlp_endpoint: str, service_name: str) -> trace.Tracer:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint.rstrip("/") + "/v1/traces"))
        )
    return provider.get_tracer("aurai.agents")


def common(agent) -> dict:
    return {"aurai.use_case.id": agent.use_case_id, "aurai.team.id": agent.team_id, "aurai.risk_tier": agent.risk_tier}


@contextmanager
def step(tracer: trace.Tracer, agent, name: str) -> Iterator[trace.Span]:
    with tracer.start_as_current_span(SPAN_AGENT_STEP) as span:
        span.set_attributes({**common(agent), "aurai.agent.name": agent.name, "aurai.agent.framework": agent.framework})
        span.set_attribute("aurai.agent.step", name)
        yield span


def approval_span(tracer: trace.Tracer, agent, outcome: str) -> None:
    """One span per observation of an approval: pending while it waits, approved or rejected when decided."""
    with tracer.start_as_current_span(SPAN_APPROVAL) as span:
        span.set_attributes({**common(agent), "aurai.approval.required": True, "aurai.approval.outcome": outcome})
