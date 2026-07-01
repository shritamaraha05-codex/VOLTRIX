#!/bin/bash
# run.sh — start VOLTRIX API locally
set -a && source .env && set +a
uvicorn main:app --reload --port 8000 --log-level info