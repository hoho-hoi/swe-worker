FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update -y \
  && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.7.2

RUN adduser --disabled-password --gecos "" agent
RUN mkdir -p /work/repo /work/state /work/logs /work/out \
  && chown -R agent:agent /work

WORKDIR /app
RUN chown -R agent:agent /app

USER agent

COPY --chown=agent:agent pyproject.toml /app/pyproject.toml
# If uv.lock exists, use frozen mode for reproducible builds.
COPY --chown=agent:agent uv.lock /app/uv.lock
# Only install runtime dependencies; the project itself is executed from source under /app.
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=agent:agent app /app/app
COPY --chown=agent:agent README.md /app/README.md

EXPOSE 8000

# Run the server from /work (consistent with container-local work root),
# while importing the application code from /app.
ENV PYTHONPATH=/app
WORKDIR /work
CMD ["/app/.venv/bin/python", "-m", "app.worker_server"]

