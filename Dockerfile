FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update -y \
  && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" agent
RUN mkdir -p /work/repo /work/state /work/logs /work/out \
  && chown -R agent:agent /work

WORKDIR /app
RUN pip install --no-cache-dir uv==0.7.2

COPY pyproject.toml /app/pyproject.toml
# If uv.lock exists, use frozen mode for reproducible builds.
COPY uv.lock /app/uv.lock
RUN uv sync --frozen --no-dev

COPY app /app/app
COPY README.md /app/README.md

EXPOSE 8000

USER agent
CMD ["uv", "run", "python", "-m", "app.worker_server"]

