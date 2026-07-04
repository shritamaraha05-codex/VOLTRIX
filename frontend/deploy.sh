#!/bin/bash
# deploy_frontend.sh — Deploy VOLTRIX frontend to your static host
# Owner: Mrinmoy
#
# The frontend is a single static HTML file (index.html) with zero build step.
# Deploy it to any static file host: Vercel, Netlify, Cloudflare Pages,
# GitHub Pages, or even a simple Nginx/Apache server.
#
# Usage:
#   1. Edit config.js and set window.__BACKEND_URL to your deployed backend URL
#   2. Run this script (or manually upload frontend/ to your host)
#   3. Open the deployed URL

set -e

# ─── Config — edit these ──────────────────────────────────────────────────────
# Choose one deploy target: "vercel", "netlify", "custom"
DEPLOY_TARGET="custom"

# For custom: the directory to copy files to
PUBLISH_DIR="./dist"

echo "▶ Preparing frontend for deployment..."

# Create clean output directory
rm -rf "${PUBLISH_DIR}"
mkdir -p "${PUBLISH_DIR}"

# Copy static assets
cp index.html "${PUBLISH_DIR}/"
cp config.js "${PUBLISH_DIR}/"

echo "   Copied index.html + config.js → ${PUBLISH_DIR}/"

if [ "$DEPLOY_TARGET" = "vercel" ]; then
  echo "   Deploying to Vercel..."
  npx vercel --prod "${PUBLISH_DIR}" || echo "   Install Vercel CLI: npm i -g vercel"

elif [ "$DEPLOY_TARGET" = "netlify" ]; then
  echo "   Deploying to Netlify..."
  npx netlify deploy --prod --dir="${PUBLISH_DIR}" || echo "   Install Netlify CLI: npm i -g netlify-cli"

else
  echo ""
  echo "✅ Frontend ready at ${PUBLISH_DIR}/"
  echo ""
  echo "Upload the ${PUBLISH_DIR}/ directory to your static host."
  echo "Or serve locally for testing:"
  echo "   npx serve ${PUBLISH_DIR}"
  echo ""
  echo "Backend CORS: make sure the backend's ALLOWED_ORIGINS env var"
  echo "includes your frontend URL. For example:"
  echo "   ALLOWED_ORIGINS=\"https://voltrix.vercel.app,https://voltrix.pages.dev\""
  echo ""
  echo "Frontend config: edit config.js to set window.__BACKEND_URL,"
  echo "or override via query param: index.html?api=https://your-api-url"
fi
