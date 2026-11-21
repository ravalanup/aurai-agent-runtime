import jwt
import pytest

from aurai_agents.approval_service import ApprovalService
from aurai_agents.approval_store import MemoryApprovalStore
from aurai_agents.approval_tokens import args_hash
from aurai_agents.errors import ApprovalError
from aurai_agents.verify import Principal
from tests.conftest import ALICE, BOB, CAROL, DAVE, SERVICE, SIGNING_KEY, create_request


def make(service, **over):
    return service.create(SERVICE, create_request(**over))


def test_a_new_request_is_pending_with_a_server_computed_hash(service):
    a = make(service)
    view = service.view(a)
    assert view["status"] == "pending" and view["required"] == 1
    assert a.args_hash == args_hash(a.arguments)
    assert a.requester == "svc-ops" and a.on_behalf_of == "erin"


def test_creating_twice_with_the_same_key_returns_the_same_request(service):
    assert make(service).id == make(service).id


def test_the_same_key_for_a_different_call_is_a_conflict(service):
    make(service)
    with pytest.raises(ApprovalError) as e:
        make(service, arguments={"title": "Other", "body": "x", "area": "Harbor Coaster"})
    assert e.value.status_code == 409


@pytest.mark.parametrize(
    ("over", "status"),
    [
        ({"team": "finance-reporting"}, 403),
        ({"use_case": "someone-elses"}, 403),
        ({"tool": "Bad Tool"}, 422),
        ({"connector": "X"}, 422),
        ({"required": 0}, 422),
        ({"required": 9}, 422),
        ({"idempotency_key": ""}, 422),
        ({"summary": ""}, 422),
        ({"summary": "x" * 501}, 422),
    ],
)
def test_invalid_requests_are_refused(service, over, status):
    with pytest.raises(ApprovalError) as e:
        make(service, **over)
    assert e.value.status_code == status


def test_a_person_cannot_create_requests(service):
    with pytest.raises(ApprovalError) as e:
        service.create(ALICE, create_request())
    assert e.value.status_code == 403


def test_one_approval_satisfies_a_single_approver_request(service):
    a = make(service)
    service.decide(ALICE, a.id, "approve", "looks right")
    assert service.view(service.get_for_agent(SERVICE, a.id))["status"] == "approved"


def test_two_distinct_approvers_are_needed_when_two_are_required(service):
    a = make(service, required=2)
    service.decide(ALICE, a.id, "approve")
    assert service.status(service.get_for_agent(SERVICE, a.id)) == "pending"
    service.decide(BOB, a.id, "approve")
    assert service.status(service.get_for_agent(SERVICE, a.id)) == "approved"


def test_any_rejection_ends_the_request(service):
    a = make(service, required=2)
    service.decide(ALICE, a.id, "approve")
    service.decide(BOB, a.id, "reject", "wrong area")
    assert service.status(service.get_for_agent(SERVICE, a.id)) == "rejected"
    with pytest.raises(ApprovalError) as e:
        service.mint_token(SERVICE, a.id)
    assert e.value.status_code == 409


def test_nobody_decides_twice(service):
    a = make(service, required=2)
    service.decide(ALICE, a.id, "approve")
    with pytest.raises(ApprovalError, match="already decided"):
        service.decide(ALICE, a.id, "approve")


def test_the_person_who_started_the_run_cannot_approve(service):
    a = make(service, on_behalf_of="alice")
    with pytest.raises(ApprovalError) as e:
        service.decide(ALICE, a.id, "approve")
    assert e.value.status_code == 403
    service.decide(BOB, a.id, "approve")


def test_the_agents_own_account_cannot_approve(service):
    a = make(service)
    agent_with_group = Principal("svc-ops", frozenset({"approvers-ops"}))
    with pytest.raises(ApprovalError) as e:
        service.decide(agent_with_group, a.id, "approve")
    assert e.value.status_code == 403


def test_only_the_teams_approver_groups_may_decide_or_even_see_it(service):
    a = make(service)
    for outsider in (CAROL, DAVE):
        with pytest.raises(ApprovalError) as e:
            service.decide(outsider, a.id, "approve")
        assert e.value.status_code == 404
        with pytest.raises(ApprovalError):
            service.get_for_approver(outsider, a.id)
    assert service.list_pending(CAROL) == [] and service.list_pending(DAVE) == []


def test_approvers_see_pending_requests_for_their_teams_only(service):
    mine = make(service)
    decided = make(service, key="run1:1")
    service.decide(BOB, decided.id, "approve")
    pending = service.list_pending(ALICE)
    assert [p.id for p in pending] == [mine.id]


def test_requests_expire(service, clock):
    a = make(service)
    clock.now += 86_401
    assert service.status(service.get_for_agent(SERVICE, a.id)) == "expired"
    with pytest.raises(ApprovalError, match="already expired"):
        service.decide(ALICE, a.id, "approve")


def test_bad_decisions_are_refused(service):
    a = make(service)
    with pytest.raises(ApprovalError):
        service.decide(ALICE, a.id, "maybe")
    with pytest.raises(ApprovalError):
        service.decide(ALICE, a.id, "approve", "x" * 501)


def test_a_token_is_minted_only_for_an_approved_request_and_only_for_its_owner(service):
    a = make(service)
    with pytest.raises(ApprovalError, match="pending"):
        service.mint_token(SERVICE, a.id)
    service.decide(ALICE, a.id, "approve")
    other_agent = Principal("svc-other", frozenset(), "ops-insights", "ops-insights")
    with pytest.raises(ApprovalError) as e:
        service.mint_token(other_agent, a.id)
    assert e.value.status_code == 404
    token = service.mint_token(SERVICE, a.id)
    claims = jwt.decode(token, SIGNING_KEY, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["tool"] == "tickets_create_draft" and claims["args_hash"] == a.args_hash
    assert claims["team"] == "ops-insights" and claims["use_case"] == "ops-insights"
    assert claims["approvers"] == ["alice"] and claims["exp"] - claims["iat"] == 600


def test_the_token_names_every_approver(service):
    a = make(service, required=2)
    service.decide(BOB, a.id, "approve")
    service.decide(ALICE, a.id, "approve")
    claims = jwt.decode(
        service.mint_token(SERVICE, a.id), SIGNING_KEY, algorithms=["HS256"], options={"verify_exp": False}
    )
    assert claims["approvers"] == ["alice", "bob"]


def test_tokens_are_not_minted_without_limit(service):
    a = make(service)
    service.decide(ALICE, a.id, "approve")
    for _ in range(5):
        service.mint_token(SERVICE, a.id)
    with pytest.raises(ApprovalError, match="too many"):
        service.mint_token(SERVICE, a.id)


def test_each_minted_token_has_its_own_single_use_id(service):
    a = make(service)
    service.decide(ALICE, a.id, "approve")
    ids = {
        jwt.decode(service.mint_token(SERVICE, a.id), SIGNING_KEY, algorithms=["HS256"], options={"verify_exp": False})[
            "jti"
        ]
        for _ in range(3)
    }
    assert len(ids) == 3


def test_a_weak_signing_key_is_refused():
    with pytest.raises(ValueError):
        ApprovalService(MemoryApprovalStore(), "short", {})


def test_the_view_lists_decisions_without_internal_fields(service):
    a = make(service)
    service.decide(ALICE, a.id, "approve", "ok")
    view = service.view(service.get_for_agent(SERVICE, a.id))
    assert view["approved_by"] == ["alice"] and view["decisions"][0]["comment"] == "ok"
    assert "token_mints" not in view and "idempotency_key" not in view
