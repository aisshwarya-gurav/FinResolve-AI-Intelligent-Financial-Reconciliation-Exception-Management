# Single-image Dockerfile for Reconciliation Agent (FastAPI + Streamlit)
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install minimal system dependencies for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose ports for FastAPI (8001) and Streamlit (8501)
EXPOSE 8001 8501

# Health check for FastAPI service
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Default command starts the FastAPI Case Management & Webhook server
CMD ["uvicorn", "case_management.api:app", "--host", "0.0.0.0", "--port", "8001"]
