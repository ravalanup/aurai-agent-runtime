# Runbook

## Symptoms

| Symptom | Likely cause | Check |
|---|---|---|
| A run stays `waiting_approval` | Nobody has decided, or the approver is in no group for the team | `GET /v1/approvals` as an approver; `config/approvers.yaml` |
| Approver gets 404 on a request | Not in the team's approver group (404 hides existence) | the token's `groups` claim |
| Approver gets 403 | They started the run, or the agent acted for them | `on_behalf_of` on the request |
| Run `completed` with an action `failed`, code `approval_invalid` | The token was already used, expired (15-minute cap), or the arguments changed | connector audit log for the `jti`; do not retry blindly: check whether the action ran |
| Action `failed`, code `approval_service_409` | Request not approved, expired, or five tokens already minted | the request's status |
| Action `failed`, `unauthenticated` | Agent token rejected: wrong audience, missing `team_id`/`use_case_id`, or the client does not exist | `aurai-agents keycloak-plan` |
| Action `failed`, `policy_denied` | The team's connector scope does not allow it | connectors' `policy/teams.yaml` |
| Runtime fails at startup naming an environment variable | A client secret or gateway key is missing from `agents-secrets` | `kubectl describe secret agents-secrets` (names only) |
| `check` fails | Config problem; the message says which agent or team | run it locally with the same files |
| Pods crash on start with a database error | `AURAI_DATABASE_URL` wrong or the Job did not run | `kubectl logs job/aurai-agents-init-db` |

## Routine tasks

- **Add an approver group or change who approves:** edit `config/approvers.yaml`; redeploy.
- **Rotate the approval signing key:** the key is shared with the connectors, and both read it at start. Rotating means updating both Secrets and restarting both together; tokens in flight (at most 15 minutes) fail and the agent retries from `execute`. There is no overlap window for two keys yet.
- **Rotate an agent's client secret:** change it in Keycloak and in `agents-secrets`, restart the runtime.
- **Retire an agent:** remove it from `agents.yaml` and redeploy; delete its Keycloak client. Its checkpoints stay in the database until retention removes them.

## What to watch

The runtime and approval service log to stdout. Spans go to the collector: `aurai.agent.step` per step and `aurai.approval` with outcome `pending`, `approved` or `rejected`. Alert on: runs waiting longer than the approval lifetime, a spike of `approval_invalid`, and any `policy_denied` on a write.
