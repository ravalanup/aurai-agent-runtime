# Approvals

A consequential action does not run until people have approved that exact action. This service is where requests wait and decisions are made; the connectors are where the approval is enforced.

## The contract with the connectors

| Question | Answer |
|---|---|
| Who says how many approvals are needed? | The connector. The agent calls without a token and reads `approvals_required` and `args_hash` from the refusal. The runtime never duplicates tier or policy logic. |
| What does the connector accept? | A token in the `X-Aurai-Approval` header: HS256, signed with the shared `AURAI_APPROVAL_SIGNING_KEY`, bound to the tool, the SHA-256 of the canonical arguments, the team and use case, and a list of distinct approvers who are not the caller. It lives at most 15 minutes and is single use (`jti`). |
| Who signs it? | Only this service, and only for a request that is approved. |

`approval_tokens.py` here and `approvals.py` in aurai-mcp-connectors implement the two ends. `tests/test_integration_connectors.py` runs this repository's tokens against the real connector servers, so a drift between them fails a test.

## Rules the service enforces

- **Only an agent creates requests**, and only for its own team and use case (taken from verified token claims, never from the body).
- **Idempotent**: the same idempotency key with the same call returns the same request; the same key with a different call is a 409.
- **Who may decide**: a person in one of the team's approver groups (`config/approvers.yaml`, matched against the token's `groups` claim). Anyone else gets a 404, so the existence of a request is not revealed.
- **Separation of duties**: nobody decides on a request they made or that was made on their behalf (`on_behalf_of` is the person who started the run).
- **One decision each**: an approver decides once; any rejection rejects the request; the request is approved when enough *distinct* approvers approve. A T2 write needs one, a T3 write two.
- **Expiry**: a pending request expires after 24 hours and then cannot be decided or turned into a token.
- **Tokens**: minted for an approved request only, only for the agent that made it, at most five times (so a transient failure can be retried). The agent records what it executed, so a re-run does not call twice. Single use at the connector is the backstop.
- The decision record (who, what, when, comment) is part of the request.

## HTTP API (audience `aurai-approvals`)

| Call | Caller | Purpose |
|---|---|---|
| `POST /v1/approvals` | agent service account | file a request |
| `GET /v1/approvals/{id}` | the agent that filed it | status |
| `POST /v1/approvals/{id}/token` | the agent that filed it | collect a signed token once approved |
| `GET /v1/approvals` | approver | pending requests for the approver's teams |
| `GET /v1/approvals/{id}/review` | approver | the exact tool and arguments to review |
| `POST /v1/approvals/{id}/decision` | approver | `{"decision": "approve" | "reject", "comment": "..."}` |

There is no approval *interface* in this repository, only the API. To approve while developing:

```powershell
# a token for a person in approvers-ops, audience aurai-approvals (see local-development.md)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8091/v1/approvals
curl -H "Authorization: Bearer $TOKEN" http://localhost:8091/v1/approvals/<id>/review
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" `
  -d '{"decision":"approve","comment":"checked the sensor log"}' http://localhost:8091/v1/approvals/<id>/decision
```

## Changing who approves

Edit `config/approvers.yaml`, review it like code (the owner of the team's data approves the change), and redeploy; the checksum annotation rolls the pods. `aurai-agents check` fails if an agent's team has no approver group.
