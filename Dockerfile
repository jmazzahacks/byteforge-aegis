FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    git \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
# CR_PAT needed for private GitHub dependencies (mazza-base)
ARG CR_PAT
ENV CR_PAT=${CR_PAT}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY database/ ./database/
COPY admin_scripts/ ./admin_scripts/
COPY migrate_scripts/ ./migrate_scripts/
COPY setup-database.py .

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose the application port
EXPOSE 5678

# Set environment variables
ENV PYTHONPATH=/app/src
ENV PORT=5678

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5678/api/health')" || exit 1

# Run with gunicorn for production (using application factory pattern)
CMD ["gunicorn", "--bind", "0.0.0.0:5678", "--workers", "4", "--timeout", "60", "--max-requests", "1000", "--max-requests-jitter", "50", "--chdir", "src", "app:create_app()"]
