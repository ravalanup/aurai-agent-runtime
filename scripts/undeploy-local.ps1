<# Remove the agent runtime, its secret and the namespace. The aurai_agents database is kept; drop it by hand to forget runs. #>
param([string]$Namespace = "aurai-agents")
helm uninstall aurai-agents -n $Namespace
kubectl delete secret agents-secrets -n $Namespace --ignore-not-found
kubectl delete namespace $Namespace --ignore-not-found
