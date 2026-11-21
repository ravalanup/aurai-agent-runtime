import json

from langgraph.checkpoint.memory import InMemorySaver

from aurai_agents.runtime import Runtime
from tests.conftest import ALICE


def run(make_ctx, service, spans, approve=True):
    ctx = make_ctx()
    rt = Runtime({"ops-insights": ctx}, InMemorySaver())
    view = rt.start("ops-insights", {"areas": ["Harbor Coaster"]}, "erin")
    if approve:
        service.decide(ALICE, view["approvals"][0]["id"], "approve")
        rt.resume("ops-insights", view["run_id"], "erin")
    return spans.get_finished_spans()


def test_every_span_follows_the_aurai_conventions(make_ctx, service, spans):
    finished = run(make_ctx, service, spans)
    assert finished
    try:
        from aurai_governance.tracing import validate_span
    except ImportError:  # the governance library is a separate repository
        validate_span = None
    for span in finished:
        attrs = dict(span.attributes)
        assert attrs["aurai.use_case.id"] == "ops-insights" and attrs["aurai.team.id"] == "ops-insights"
        assert attrs["aurai.risk_tier"] == "T2"
        if validate_span:
            assert validate_span(span.name, attrs) == [], (span.name, attrs)


def test_agent_steps_are_named_and_approvals_follow_their_outcome(make_ctx, service, spans):
    finished = run(make_ctx, service, spans)
    steps = [s.attributes["aurai.agent.step"] for s in finished if s.name == "aurai.agent.step"]
    assert steps[:3] == ["gather", "draft", "open_approvals"] and "execute_approved" in steps
    assert all(s.attributes["aurai.agent.framework"] == "langgraph" for s in finished if s.name == "aurai.agent.step")
    outcomes = [s.attributes["aurai.approval.outcome"] for s in finished if s.name == "aurai.approval"]
    assert outcomes == ["pending", "approved"]
    assert all(s.attributes["aurai.approval.required"] is True for s in finished if s.name == "aurai.approval")


def test_a_rejection_is_recorded_as_rejected(make_ctx, service, spans):
    ctx = make_ctx()
    rt = Runtime({"ops-insights": ctx}, InMemorySaver())
    view = rt.start("ops-insights", {"areas": ["Harbor Coaster"]}, "erin")
    service.decide(ALICE, view["approvals"][0]["id"], "reject")
    rt.resume("ops-insights", view["run_id"], "erin")
    outcomes = [
        s.attributes["aurai.approval.outcome"] for s in spans.get_finished_spans() if s.name == "aurai.approval"
    ]
    assert outcomes[-1] == "rejected"


def test_no_prompt_argument_or_result_text_reaches_a_span(make_ctx, service, spans):
    finished = run(make_ctx, service, spans)
    blob = json.dumps([dict(s.attributes) for s in finished])
    for text in ("Inspect brake sensor", "Alarm recurred", "brake sensor alarm", "Harbor Coaster had repeated"):
        assert text not in blob
