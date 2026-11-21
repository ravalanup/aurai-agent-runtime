# Identity

Every call in the system carries a token that says who is calling, and each service checks the audience so a token for one service cannot be replayed at another.

| Caller | Token | Audience | Claims that matter |
|---|---|---|---|
| A person starting a run or approving | Authorization code with PKCE, client `agents-console` | `aurai-agents`, `aurai-approvals` | `sub`, `groups` |
| n8n | Client credentials, client `n8n-orchestrator` | `aurai-agents` only | `sub` |
| An agent calling connectors and the approval service | Client credentials, one client per agent (for ops-insights, `agent-ops-insights`) | `aurai-connectors`, `aurai-approvals` | `team_id`, `use_case_id` (hardcoded in Keycloak) |

## Why one client per agent

Connector policy and the audit trail work from the caller's team and use case. If agents shared a client, an agent could only be told apart by something it sent itself. Here the claims are *hardcoded protocol mappers on the client*, so the agent cannot change them and a leaked secret is limited to one use case. A person's token is not accepted where an agent's is expected: connectors need `team_id` and `use_case_id`, and the approval service only lets a service principal file requests.

## Managing the clients

`keycloak_clients.py` holds the desired state and is driven by `config/agents.yaml`: adding an agent adds its client.

```powershell
$env:AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET = "<same value as in the cluster Secret>"
$env:AURAI_N8N_CLIENT_SECRET = "<a value for the n8n credential>"
$env:KEYCLOAK_ADMIN_USER = "admin"; $env:KEYCLOAK_ADMIN_PASSWORD = "<local admin password>"
aurai-agents keycloak-plan      # what would change; prints no secrets
aurai-agents keycloak-apply     # create or update
```

`plan` compares only the settings this repository manages (flow flags, redirect URIs, and the mappers it defines), so Keycloak's own defaults are not reported as drift. A secret is set only when a client is created, from the environment variable named in `agents.yaml`; rotate it in Keycloak and the cluster Secret together.

## Approver groups

`config/approvers.yaml` names Keycloak groups (`approvers-ops`, `approvers-finance`). The groups and their members are defined in the realm managed by aurai-workspace (`realm/aurai-realm.yaml`; demo approvers `rosa.delgado` and `hana.kim`), so re-apply that realm before expecting anyone to be able to approve. The `agents-console` client puts the groups in the `groups` claim.

## Not done

- A refresh or rotation process for the agents' client secrets.
- Token exchange so a connector sees the *person* behind a run. Today it sees the agent; the person is recorded as `on_behalf_of` on the approval request.
