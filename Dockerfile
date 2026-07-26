FROM python:3.13-slim

# uv binary, pinned by immutable digest — not just the version tag. A tag can
# be re-pushed to point at a different (malicious) binary; the digest cannot.
# This binary installs everything else, so it is the root of trust for the
# build. To upgrade uv, bump BOTH the tag and the digest.
COPY --from=ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb /uv /uvx /bin/

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    git \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-private.txt uv.toml ./

# Locked, hash-verified third-party dependencies. Every PyPI distribution is
# checked against its SHA256 in requirements.txt; uv.toml's exclude-newer
# gate also refuses anything published in the last 7 days. Git URLs in the
# lock are token-free — no credential appears in any committed file.
RUN uv pip install --system -r requirements.txt

# First-party libs at latest commit (deliberately floating — see
# requirements-private.txt). The CR_PAT secret is mounted only for this
# step: it never enters a layer, the image env, or build history. --no-deps
# because the sub-tree is locked above; --no-config so the exclude-newer
# gate can't reject one of our own commits pushed within the window.
RUN --mount=type=secret,id=cr_pat \
    export CR_PAT=$(cat /run/secrets/cr_pat) && \
    git config --global url."https://${CR_PAT}@github.com/".insteadOf "https://github.com/" && \
    uv pip install --system --no-config --no-deps -r requirements-private.txt && \
    git config --global --unset url."https://${CR_PAT}@github.com/".insteadOf

# Copy application code
COPY src/ ./src/
COPY database/ ./database/
COPY admin_scripts/ ./admin_scripts/
COPY migrate_scripts/ ./migrate_scripts/

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
