# The generator image. S3a carries the agent only; S3b adds a JRE and JMeter.
# Nothing here downloads anything at run time -- an air-gapped deployment is a
# stated v1.0 goal and is cheap to keep, expensive to recover.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
WORKDIR /srv

COPY pyproject.toml uv.lock ./
# Every workspace member's manifest, because uv resolves the whole workspace
# before it installs any of it. Only the agent's source follows, so --package
# is what keeps this image to the agent rather than the control plane.
COPY apps/agent/pyproject.toml apps/agent/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
COPY packages/contracts/python/pyproject.toml packages/contracts/python/
COPY packages/executor-sdk/pyproject.toml packages/executor-sdk/
RUN uv sync --locked --no-dev --no-install-workspace --package plimsoll-agent

COPY packages/contracts/python packages/contracts/python
COPY packages/executor-sdk packages/executor-sdk
COPY apps/agent apps/agent
RUN uv sync --locked --no-dev --package plimsoll-agent

RUN useradd --uid 10001 --create-home plimsoll && chown -R 10001:10001 /srv
ENV HOME=/home/plimsoll UV_NO_SYNC=1 UV_FROZEN=1
USER 10001
CMD ["uv", "run", "python", "-m", "plimsoll_agent"]
