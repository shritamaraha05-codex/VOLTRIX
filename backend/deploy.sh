#!/bin/bash
# deploy.sh — Build and deploy to Cloud Run in one command
# Owner: Mrinmoy
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Prereqs:
#   gcloud auth login
#   gcloud config set project YOUR_PROJECT_ID
#   .env.production file with all env vars (see below)

set -e  # exit on any error

# ─── Config — edit these ──────────────────────────────────────────────────────
PROJECT_ID="YOUR_PROJECT_ID"       # e.g. gridsense-buildx-2026
SERVICE_NAME="gridsense-backend"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# ─── Step 1: Build & push image ───────────────────────────────────────────────
echo "▶ Building and pushing Docker image..."
gcloud builds submit \
  --tag "${IMAGE}" \
  --project "${PROJECT_ID}"

# ─── Step 2: Deploy to Cloud Run ──────────────────────────────────────────────
echo "▶ Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 60 \
  --concurrency 80 \
  --set-env-vars "\
DATABASE_URL=${DATABASE_URL},\
GCP_PROJECT=${PROJECT_ID},\
GCP_LOCATION=${REGION},\
RESEND_API_KEY=${RESEND_API_KEY},\
ALLOWED_ORIGINS=${ALLOWED_ORIGINS}"

echo ""
echo "✅ Deployed. Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --platform managed \
  --region "${REGION}" \
  --format "value(status.url)"
