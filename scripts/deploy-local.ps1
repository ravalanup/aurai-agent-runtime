<#
.SYNOPSIS
  Build and install the Aurai agent runtime and approval service on the local kind cluster.

.DESCRIPTION
  Prerequisites
    - aurai-infra running (Keycloak, Postgres, OTel collector) and a kind cluster. Docker, kind, kubectl, helm.
    - aurai-mcp-connectors installed (its connectors-secrets Secret supplies the approval signing key, so both sides
      share it), and aurai-gateway installed for the model.
    - A gateway virtual key for the ops-insights use case (-GatewayKey, or AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY).
  What it does
    1. Builds the image, runs `check` inside it, and loads it into kind.
    2. Creates the namespace (labelled for platform access) and the aurai_agents database in the shared Postgres.
    3. Creates the agents-secrets Secret: the approval signing key (taken from the connectors' Secret unless given),
       the database URL, the agent's Keycloak client secret (generated and printed once unless given) and gateway key.
    4. Installs the chart with config\agents.yaml and config\approvers.yaml.
  After it finishes, create the Keycloak clients (docs\identity.md):
    $env:AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET = "<the secret printed above>"; $env:AURAI_N8N_CLIENT_SECRET = "<any>"
    aurai-agents keycloak-apply
#>
param(
  [string]$ClusterName = "aurai",
  [string]$Namespace = "aurai-agents",
  [string]$Image = "aurai-agent-runtime:0.1.0",
  [string]$ApprovalKey = $env:AURAI_APPROVAL_SIGNING_KEY,
  [string]$GatewayKey = $env:AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY,
  [string]$ClientSecret = $env:AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

foreach ($tool in "docker", "kind", "kubectl", "helm") {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing required tool: $tool" }
}
if (-not $GatewayKey) { throw "Pass -GatewayKey (the gateway virtual key for ops-insights)." }

kubectl config use-context "kind-$ClusterName" | Out-Null

if (-not $ApprovalKey) {
  $encoded = kubectl get secret connectors-secrets -n aurai-connectors -o jsonpath='{.data.AURAI_APPROVAL_SIGNING_KEY}' 2>$null
  if (-not $encoded) { throw "No approval key: install aurai-mcp-connectors first, or pass -ApprovalKey." }
  $ApprovalKey = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
}
if (-not $ClientSecret) {
  $bytes = New-Object byte[] 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $ClientSecret = [Convert]::ToBase64String($bytes)
  Write-Host "Generated the ops-insights Keycloak client secret. Keep it: keycloak-apply needs the same value."
  Write-Host "  AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET=$ClientSecret"
}

Write-Host "Building $Image"
docker build -t $Image $repo
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

Write-Host "Checking the agent configuration"
docker run --rm -e "AURAI_APPROVAL_SIGNING_KEY=$ApprovalKey" $Image check
if ($LASTEXITCODE -ne 0) { throw "configuration check failed; fix the reported problems before deploying" }

kind load docker-image $Image --name $ClusterName
if ($LASTEXITCODE -ne 0) { throw "kind load failed" }

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null
kubectl label namespace $Namespace aurai.io/platform-access=true --overwrite | Out-Null

# A separate database, so the checkpoint tables never share a schema with another service.
$pgPassword = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(
  (kubectl get secret aurai-postgres -n aurai-system -o jsonpath='{.data.POSTGRES_PASSWORD}')))
$exists = kubectl exec -n aurai-system statefulset/postgres -- psql -U aurai -d aurai -tAc "SELECT 1 FROM pg_database WHERE datname='aurai_agents'"
if ("$exists".Trim() -ne "1") {
  kubectl exec -n aurai-system statefulset/postgres -- psql -U aurai -d aurai -c "CREATE DATABASE aurai_agents" | Out-Null
}
$dbUrl = "postgresql://aurai:$([uri]::EscapeDataString($pgPassword))@postgres.aurai-system.svc.cluster.local:5432/aurai_agents"

@"
apiVersion: v1
kind: Secret
metadata: { name: agents-secrets, namespace: $Namespace }
type: Opaque
stringData:
  AURAI_APPROVAL_SIGNING_KEY: "$ApprovalKey"
  AURAI_DATABASE_URL: "$dbUrl"
  AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET: "$ClientSecret"
  AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY: "$GatewayKey"
"@ | kubectl apply -f - | Out-Null

$repository, $tag = $Image.Split(":")
helm upgrade --install aurai-agents "$repo\charts\aurai-agents" -n $Namespace `
  -f "$repo\charts\aurai-agents\values-local.yaml" `
  --set image.repository=$repository --set image.tag=$tag `
  --set-file files.agents="$repo\config\agents.yaml" `
  --set-file files.approvers="$repo\config\approvers.yaml" --wait --timeout 4m
if ($LASTEXITCODE -ne 0) { throw "helm install failed" }

kubectl get pods -n $Namespace
Write-Host "Done. Runtime: http://agent-runtime.$Namespace.svc.cluster.local:8080   Approvals: http://approvals.$Namespace.svc.cluster.local:8080"
Write-Host "Next: create the Keycloak clients (see the .DESCRIPTION of this script), then scripts\port-forward.ps1."
