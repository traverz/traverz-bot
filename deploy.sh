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
    --min-instances 1 \
    --timeout 300 \
    --session-affinity \
    --port 8765 \
    --vpc-connector traverz-connector \
    --vpc-egress private-ranges-only \
    --set-env-vars "TRAVERZ_BACKEND_URL=https://api.traverz.ai,TRAVERZ_WORKSPACE_PATH=/home/traverz/.traverz/workspace,GCP_PROJECT_ID=${PROJECT_ID}" \
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,WEBSOCKET_TOKEN_SECRET=WEBSOCKET_TOKEN_SECRET:latest,TRAVERZ_SKILLS_RELOAD_SECRET=TRAVERZ_SKILLS_RELOAD_SECRET:latest" \
    --project "$PROJECT_ID"

echo "==> Done."
gcloud run services describe traverz-bot --region "$REGION" --project "$PROJECT_ID" \
    --format "value(status.url)"
