#!/bin/bash
# deploy_frontend.sh — Deploy VOLTRIX frontend
# Owner: Mrinmoy
#
# Builds with Vite and deploys the output.
# For production, set window.__BACKEND_URL in config.js or pass ?api=... query param.

set -e

echo "▶ Building frontend with Vite..."
npm run build

echo ""
echo "✅ Build complete → dist/"
echo ""
echo "Deploy the dist/ directory to your static host."
echo "Or serve locally:"
echo "   npm run preview"
echo ""
echo "For development:"
echo "   npm run dev"
