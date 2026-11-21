<# Port-forward the runtime (8090) or the approval service (8091) for direct testing (docs\local-development.md). #>
param(
  [ValidateSet("runtime", "approvals")][string]$Service = "runtime",
  [string]$Namespace = "aurai-agents"
)
$ErrorActionPreference = "Stop"
$name = if ($Service -eq "runtime") { "agent-runtime" } else { "approvals" }
$port = if ($Service -eq "runtime") { 8090 } else { 8091 }
Write-Host "$Service at http://localhost:$port   (Ctrl+C to stop)"
kubectl port-forward -n $Namespace "svc/$name" "${port}:8080"
