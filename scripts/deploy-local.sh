#!/usr/bin/env bash
# Build and install the Aurai agent runtime and approval service on the local kind cluster. Mirrors deploy-local.ps1.
# Requires: docker, kind, kubectl, helm; aurai-infra, aurai-mcp-connectors and aurai-gateway installed.
# AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY is required. The approval key defaults to the connectors' key.
set -euo pipefail
CLUSTER="${CLUSTER:-aurai}"; NS="${NS:-aurai-agents}"; IMAGE="${IMAGE:-aurai-agent-runtime:0.1.0}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
for t in docker kind kubectl helm; do command -v "$t" >/dev/null || { echo "missing $t" >&2; exit 1; }; done
: "${AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY:?set the gateway virtual key for ops-insights}"

kubectl config use-context "kind-$CLUSTER" >/dev/null
if [ -z "${AURAI_APPROVAL_SIGNING_KEY:-}" ]; then
  AURAI_APPROVAL_SIGNING_KEY="$(kubectl get secret connectors-secrets -n aurai-connectors \
    -o jsonpath='{.data.AURAI_APPROVAL_SIGNING_KEY}' | base64 -d)"
fi
if [ -z "${AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET:-}" ]; then
  AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET="$(head -c 24 /dev/urandom | base64)"
  echo "Generated the ops-insights Keycloak client secret. Keep it: keycloak-apply needs the same value."
  echo "  AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET=$AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET"
fi

docker build -t "$IMAGE" "$REPO"
docker run --rm -e "AURAI_APPROVAL_SIGNING_KEY=$AURAI_APPROVAL_SIGNING_KEY" "$IMAGE" check
kind load docker-image "$IMAGE" --name "$CLUSTER"

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl label namespace "$NS" aurai.io/platform-access=true --overwrite >/dev/null

PGPASS="$(kubectl get secret aurai-postgres -n aurai-system -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
if [ "$(kubectl exec -n aurai-system statefulset/postgres -- psql -U aurai -d aurai -tAc \
  "SELECT 1 FROM pg_database WHERE datname='aurai_agents'")" != "1" ]; then
  kubectl exec -n aurai-system statefulset/postgres -- psql -U aurai -d aurai -c "CREATE DATABASE aurai_agents" >/dev/null
fi
PGPASS_ENC="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$PGPASS")"
DB_URL="postgresql://aurai:${PGPASS_ENC}@postgres.aurai-system.svc.cluster.local:5432/aurai_agents"

kubectl apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata: { name: agents-secrets, namespace: $NS }
type: Opaque
stringData:
  AURAI_APPROVAL_SIGNING_KEY: "$AURAI_APPROVAL_SIGNING_KEY"
  AURAI_DATABASE_URL: "$DB_URL"
  AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET: "$AURAI_AGENT_OPS_INSIGHTS_CLIENT_SECRET"
  AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY: "$AURAI_AGENT_OPS_INSIGHTS_GATEWAY_KEY"
YAML

helm upgrade --install aurai-agents "$REPO/charts/aurai-agents" -n "$NS" \
  -f "$REPO/charts/aurai-agents/values-local.yaml" \
  --set image.repository="${IMAGE%%:*}" --set image.tag="${IMAGE##*:}" \
  --set-file files.agents="$REPO/config/agents.yaml" \
  --set-file files.approvers="$REPO/config/approvers.yaml" --wait --timeout 4m
kubectl get pods -n "$NS"
echo "Done. Create the Keycloak clients with: aurai-agents keycloak-apply (see docs/identity.md)"
