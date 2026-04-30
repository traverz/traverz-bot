#!/bin/sh
# Manual deploy script for traverz-bot to Cloud Run
# Usage: ./deploy.sh [project_id] [region]
set -e

PROJECT_ID="${1:-traverz-prod}"
REGION="${2:-asia-southeast1}"
IMAGE="gcr.io/${PROJECT_ID}/traverz-bot:latest"

echo "==> Configuring Docker credentials for GCR..."
gcloud auth configure-docker --quiet

echo "==> Building & pushing Traverz bot image (linux/amd64 for Cloud Run)..."
# Cloud Run requires a linux/amd64 image and rejects OCI image indexes.
# --provenance=false ensures buildx pushes a single-arch image manifest
# instead of an OCI index (which Cloud Run does not support).
docker buildx build \
    --platform linux/amd64 \
    --provenance=false \
    -f Dockerfile.traverz \
    -t "$IMAGE" \
    --push \
    .

echo "==> Deploying to Cloud Run ..."
gcloud run deploy traverz-bot \
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
    --set-env-vars "TRAVERZ_BACKEND_URL=https://api.traverz.ai,TRAVERZ_GCS_BUCKET=traverz-bot-workspace" \
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,TRAVERZ_WS_TOKEN=TRAVERZ_WS_TOKEN:latest" \
    --project "$PROJECT_ID"

echo "==> Done."
gcloud run services describe traverz-bot --region "$REGION" --project "$PROJECT_ID" \
    --format "value(status.url)"
