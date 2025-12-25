FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update -y \
  && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir uv==0.7.2

COPY pyproject.toml /app/pyproject.toml
# If uv.lock exists, use frozen mode for reproducible builds.
COPY uv.lock /app/uv.lock
RUN uv sync --frozen --no-dev

COPY app /app/app
COPY README.md /app/README.md

ENV DATA_DIR=/data
EXPOSE 8000

CMD ["uv", "run", "python", "-m", "app.worker_server"]

