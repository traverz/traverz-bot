#!/bin/sh
# Manual deploy script for traverz-bot to Cloud Run
# Usage: ./deploy.sh [project_id] [region]
set -e

PROJECT_ID="${1:-traverz-prod}"
REGION="${2:-asia-southeast1}"
IMAGE="gcr.io/${PROJECT_ID}/traverz-bot:latest"

echo "==> Configuring Docker credentials for GCR..."
gcloud config set account jason@traverz.ai
gcloud auth configure-docker --quiet

echo "==> Building Traverz bot image (linux/amd64 for Cloud Run)..."
# Cloud Run requires a linux/amd64 image and rejects OCI image indexes.
# --provenance=false ensures buildx produces a single-arch manifest.
# --load builds into the local Docker image store rather than pushing inline;
# this avoids the BuildKit daemon credential isolation bug (BuildKit runs in
# its own container and does not reliably inherit gcloud's credential helper).
docker buildx build \
    --platform linux/amd64 \
    --provenance=false \
    -f Dockerfile.traverz \
    -t "$IMAGE" \
    --load \
    .

echo "==> Pushing image to GCR..."
# The gcloud credential helper (configured above) handles auth for docker push.
docker push "$IMAGE"

echo "==> Clearing any existing startup/liveness probes from Cloud Run service..."
_PROBE_YAML=$(mktemp /tmp/cr-traverz-bot-XXXXXX.yaml)
if gcloud run services describe traverz-bot \
        --region "$REGION" --project "$PROJECT_ID" \
        --format=export 2>/dev/null > "$_PROBE_YAML" && [ -s "$_PROBE_YAML" ]; then
    python3 << 'PYEOF' "$_PROBE_YAML"
import sys
path = sys.argv[1]
out, skip_indent = [], None
with open(path) as f:
    for line in f:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith(('livenessProbe:', 'startupProbe:')):
            skip_indent = indent
            continue
        if skip_indent is not None:
            if stripped and indent <= skip_indent:
                skip_indent = None
            else:
                continue
        out.append(line)
with open(path, 'w') as f:
    f.writelines(out)
PYEOF
    gcloud run services replace "$_PROBE_YAML" \
        --region "$REGION" --project "$PROJECT_ID" 2>/dev/null \
        && echo "  Probes cleared." || echo "  (could not clear probes — service may not exist yet)"
fi
rm -f "$_PROBE_YAML"

echo "==> Deploying to Cloud Run ..."
gcloud beta run deploy traverz-bot \
    --image "$IMAGE" \
    --region "$REGION" \
    --platform managed \
    --allow-unauthenticated \
    --execution-environment gen2 \
    --cpu 1 \
    --memory 1Gi \
    --concurrency 80 \
    --timeout 300 \
    --port 8765 \
    --vpc-connector traverz-connector \
    --vpc-egress private-ranges-only \
    --set-env-vars "TRAVERZ_BACKEND_URL=https://api.traverz.ai,TRAVERZ_WORKSPACE_PATH=/home/traverz/.traverz/workspace" \
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,WEBSOCKET_TOKEN_SECRET=WEBSOCKET_TOKEN_SECRET:latest" \
    --project "$PROJECT_ID"

echo "==> Done."
gcloud run services describe traverz-bot --region "$REGION" --project "$PROJECT_ID" \
    --format "value(status.url)"
