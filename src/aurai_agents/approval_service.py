"""The approval service: where a consequential action waits for people.

An agent that needs approval (because a connector said so) creates a request holding the exact tool and arguments.
Approvers in the right group review that and approve or reject. When enough distinct people approve, the requesting
agent can collect a signed token for exactly that call and the connector will run it once.

Rules enforced here:
  - the request must come from an agent service account, for its own team and use case;
  - only members of the team's approver groups may decide;
  - nobody approves their own request: not the agent's account, and not the person who started the run;
  - each approver decides once; any rejection ends the request;
  - requests and approvals expire; a token can be minted only for an approved, unexpired request."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass

from .approval_store import Approval, ApprovalStore
from .approval_tokens import args_hash, mint
from .errors import ApprovalError
from .verify import Principal

TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{2,63}")
CONNECTOR_NAME = re.compile(r"[a-z][a-z0-9-]{1,31}")
MAX_REQUIRED = 3
MAX_MINTS = 5


@dataclass(frozen=True)
class CreateRequest:
    idempotency_key: str
    connector: str
    tool: str
    arguments: dict
    team: str
    use_case: str
    summary: str
    required: int
    on_behalf_of: str | None = None


class ApprovalService:
    def __init__(
        self,
        store: ApprovalStore,
        signing_key: str,
        approver_groups: dict[str, list[str]],
        clock=time.time,
        request_ttl_seconds: int = 86400,
        token_lifetime_seconds: int = 600,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("the approval signing key must be at least 32 characters")
        self._store, self._key, self._groups = store, signing_key, approver_groups
        self._clock, self._ttl, self._token_ttl = clock, request_ttl_seconds, token_lifetime_seconds

    # ---- views ------------------------------------------------------------------------------------------------

    def status(self, a: Approval) -> str:
        if any(d["decision"] == "reject" for d in a.decisions):
            return "rejected"
        if len({d["approver"] for d in a.decisions if d["decision"] == "approve"}) >= a.required:
            return "approved"
        return "expired" if self._clock() >= a.expires_at else "pending"

    def view(self, a: Approval) -> dict:
        return {
            "id": a.id,
            "status": self.status(a),
            "connector": a.connector,
            "tool": a.tool,
            "arguments": a.arguments,
            "args_hash": a.args_hash,
            "team": a.team,
            "use_case": a.use_case,
            "summary": a.summary,
            "required": a.required,
            "on_behalf_of": a.on_behalf_of,
            "approved_by": sorted({d["approver"] for d in a.decisions if d["decision"] == "approve"}),
            "decisions": [{k: d[k] for k in ("approver", "decision", "comment", "at")} for d in a.decisions],
            "created_at": a.created_at,
            "expires_at": a.expires_at,
        }

    # ---- agent side -------------------------------------------------------------------------------------------

    def create(self, caller: Principal, req: CreateRequest) -> Approval:
        if not caller.is_service:
            raise ApprovalError(403, "only an agent service account can request approvals")
        if (req.team, req.use_case) != (caller.team_id, caller.use_case_id):
            raise ApprovalError(403, "a request must be for the caller's own team and use case")
        if not TOOL_NAME.fullmatch(req.tool) or not CONNECTOR_NAME.fullmatch(req.connector):
            raise ApprovalError(422, "invalid connector or tool name")
        if not 1 <= req.required <= MAX_REQUIRED:
            raise ApprovalError(422, f"required approvals must be between 1 and {MAX_REQUIRED}")
        if not req.idempotency_key or len(req.idempotency_key) > 200:
            raise ApprovalError(422, "an idempotency key of at most 200 characters is required")
        if not req.summary or len(req.summary) > 500:
            raise ApprovalError(422, "a summary of at most 500 characters is required")
        digest = args_hash(req.arguments)
        existing = self._store.get_by_key(req.team, req.use_case, req.idempotency_key)
        if existing:
            if (existing.tool, existing.args_hash) != (req.tool, digest):
                raise ApprovalError(409, "that idempotency key was used for a different call")
            return existing
        now = self._clock()
        approval = Approval(
            id=uuid.uuid4().hex,
            idempotency_key=req.idempotency_key,
            connector=req.connector,
            tool=req.tool,
            arguments=req.arguments,
            args_hash=digest,
            team=req.team,
            use_case=req.use_case,
            requester=caller.subject,
            on_behalf_of=req.on_behalf_of,
            summary=req.summary,
            required=req.required,
            created_at=now,
            expires_at=now + self._ttl,
        )
        if not self._store.create(approval):  # lost a race with an identical request
            existing = self._store.get_by_key(req.team, req.use_case, req.idempotency_key)
            assert existing is not None
            return existing
        return approval

    def get_for_agent(self, caller: Principal, approval_id: str) -> Approval:
        approval = self._store.get(approval_id)
        if not approval or not self._owns(caller, approval):
            raise ApprovalError(404, "no such approval")
        return approval

    def mint_token(self, caller: Principal, approval_id: str) -> str:
        with self._store.locked(approval_id) as approval:
            if not approval or not self._owns(caller, approval):
                raise ApprovalError(404, "no such approval")
            state = self.status(approval)
            if state != "approved":
                raise ApprovalError(409, f"the approval is {state}, not approved")
            if self._clock() >= approval.expires_at:
                raise ApprovalError(409, "the approval has expired")
            if approval.token_mints >= MAX_MINTS:
                raise ApprovalError(409, "too many tokens were issued for this approval")
            approval.token_mints += 1
            approvers = sorted({d["approver"] for d in approval.decisions if d["decision"] == "approve"})
            return mint(
                self._key,
                tool=approval.tool,
                arguments=approval.arguments,
                team=approval.team,
                use_case=approval.use_case,
                approvers=approvers,
                lifetime_seconds=self._token_ttl,
                now=self._clock(),
            )

    @staticmethod
    def _owns(caller: Principal, approval: Approval) -> bool:
        return (caller.team_id, caller.use_case_id, caller.subject) == (
            approval.team,
            approval.use_case,
            approval.requester,
        )

    # ---- approver side ----------------------------------------------------------------------------------------

    def _may_decide(self, approver: Principal, approval: Approval) -> bool:
        return bool(approver.groups & set(self._groups.get(approval.team, [])))

    def list_pending(self, approver: Principal) -> list[Approval]:
        teams = [t for t, groups in self._groups.items() if approver.groups & set(groups)]
        out = []
        for team in teams:
            out += [a for a in self._store.list_for_team(team) if self.status(a) == "pending"]
        return sorted(out, key=lambda a: a.created_at)

    def get_for_approver(self, approver: Principal, approval_id: str) -> Approval:
        approval = self._store.get(approval_id)
        if not approval or not self._may_decide(approver, approval):
            raise ApprovalError(404, "no such approval")
        return approval

    def decide(self, approver: Principal, approval_id: str, decision: str, comment: str = "") -> Approval:
        if decision not in ("approve", "reject"):
            raise ApprovalError(422, "decision must be approve or reject")
        if len(comment) > 500:
            raise ApprovalError(422, "comment must be at most 500 characters")
        with self._store.locked(approval_id) as approval:
            if not approval or not self._may_decide(approver, approval):
                raise ApprovalError(404, "no such approval")
            if approver.subject in {approval.requester, approval.on_behalf_of}:
                raise ApprovalError(403, "you cannot decide on a request you or your agent made")
            state = self.status(approval)
            if state != "pending":
                raise ApprovalError(409, f"the request is already {state}")
            if any(d["approver"] == approver.subject for d in approval.decisions):
                raise ApprovalError(409, "you have already decided on this request")
            approval.decisions.append(
                {"approver": approver.subject, "decision": decision, "comment": comment, "at": self._clock()}
            )
            return approval
