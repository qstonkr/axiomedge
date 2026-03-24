FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# Install UV
COPY --from=ghcr.io/astral-sh/uv:0.5.20 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# Application code
COPY src/ src/
COPY cli/ cli/
COPY dashboard/ dashboard/

# --- API Server ---
FROM base AS api
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# --- Dashboard ---
FROM base AS dashboard
EXPOSE 8501
CMD ["uv", "run", "streamlit", "run", "dashboard/app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
