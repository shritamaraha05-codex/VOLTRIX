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
PROJECT_ID="voltrix-501614"       # e.g. voltrix-app
SERVICE_NAME="voltrix-backend"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# ─── Step 1: Build & push image ───────────────────────────────────────────────
echo "▶ Building and pushing Docker image..."
gcloud builds submit \
  --tag "${IMAGE}" \
  --project "${PROJECT_ID}"

# ─── Step 2: Deploy to Cloud Run with env vars ────────────────────────────────
# Prereq: export the following vars in your shell or set them in CI/CD secrets:
#   DATABASE_URL  GCP_PROJECT  GEMINI_API_KEY
#   SMTP_HOST  SMTP_PORT  SMTP_USER  SMTP_PASSWORD  SMTP_FROM  VOLTRIX_OPS_EMAIL
#   ALLOWED_ORIGINS
#
# Example:
#   export DATABASE_URL="postgresql://..."
#   export GEMINI_API_KEY="AIza..."
#   export SMTP_HOST="smtp.gmail.com" SMTP_PORT="587"
#   export SMTP_USER="your@gmail.com" SMTP_PASSWORD="your-app-password"
#   export SMTP_FROM="VOLTRIX <your@gmail.com>"
#   export VOLTRIX_OPS_EMAIL="ops@ward.gov.in"
#   export ALLOWED_ORIGINS="http://localhost:5173,https://voltrix.vercel.app"

echo "▶ Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 120 \
  --concurrency 40 \
  --set-env-vars "\
DATABASE_URL=${DATABASE_URL},\
GCP_PROJECT=${PROJECT_ID},\
GEMINI_API_KEY=${GEMINI_API_KEY},\
SMTP_HOST=${SMTP_HOST},\
SMTP_PORT=${SMTP_PORT},\
SMTP_USER=${SMTP_USER},\
SMTP_PASSWORD=${SMTP_PASSWORD},\
SMTP_FROM=${SMTP_FROM},\
VOLTRIX_OPS_EMAIL=${VOLTRIX_OPS_EMAIL},\
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000,null,https://voltrix-psi.vercel.app"

echo ""
echo "✅ Deployed. Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --platform managed \
  --region "${REGION}" \
  --format "value(status.url)"
