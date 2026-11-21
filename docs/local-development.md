# Local development

## Without a cluster

```powershell
pip install -e ".[dev]"
ruff check .
pytest -q
$env:AURAI_APPROVAL_SIGNING_KEY = "local-only-signing-key-0123456789abcdef"; aurai-agents check
```

Optional extras that switch on more tests:

| To run | Do this |
|---|---|
| Postgres tests (checkpointer, approval store, concurrent decisions) | `$env:AURAI_TEST_POSTGRES_URL = "postgresql://postgres:pw@localhost:5432/test"` |
| Tests against the real connector servers | `pip install -e ../aurai-mcp-connectors` and have the `opa` binary on the path |
| Governance span validation | `pip install -e ../aurai-eval-governance` |

Without them those tests are skipped, and the summary says how many.

## On the local kind cluster

Prerequisites: aurai-infra (Keycloak, Postgres, collector), aurai-gateway, aurai-mcp-connectors, Docker, kind, kubectl, helm.

```powershell
.\scripts\deploy-local.ps1 -GatewayKey "<virtual key for ops-insights from the gateway>"
```

The script builds the image, runs `check` inside it, creates the `aurai_agents` database in the shared Postgres, creates the `agents-secrets` Secret (taking the approval key from the connectors' Secret so both sides match) and installs the chart. It prints the generated Keycloak client secret once; keep it.

Then create the Keycloak clients ([identity.md](identity.md)) and reach the services:

```powershell
.\scripts\port-forward.ps1 -Service runtime     # http://localhost:8090
.\scripts\port-forward.ps1 -Service approvals   # http://localhost:8091
```

A run needs a token for the `agents-console` client and a person in `approvers-ops` to approve. Re-apply the aurai-workspace realm first so `approvers-ops` and its demo user `rosa.delgado` exist.

Remove everything with `.\scripts\undeploy-local.ps1`. The database is kept.
