# GridSense Backend — Dockerfile
# Owner: Mrinmoy
# Builds a production-ready image for Cloud Run deployment.

FROM python:3.11-slim

# System deps needed by Prophet (pystan) and psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Cloud Run injects PORT; uvicorn must bind to it.
# Do not hardcode 8080 — use $PORT so local dev with a different port also works.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
