FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# verify and version pinning shell out to git: ls-remote to resolve a ref, and
# a blobless sparse fetch to read the plan.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# Pinned to the uv that writes uv.lock, so `--frozen` can read it.
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
WORKDIR /srv

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
# Manifest only. uv resolves every workspace member before installing any of
# them, dev group included, but --no-dev leaves the agent uninstalled -- so the
# control plane never carries the code that runs beside a customer's plan.
COPY apps/agent/pyproject.toml apps/agent/
COPY packages/contracts/python/pyproject.toml packages/contracts/python/
COPY packages/executor-sdk/pyproject.toml packages/executor-sdk/
# Third-party dependencies only. The workspace packages must be skipped here:
# their source is not copied yet, so an editable build would record a wheel
# with no path hook and the later sync would consider them already installed.
RUN uv sync --locked --no-dev --no-install-workspace

COPY packages/contracts/python packages/contracts/python
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN uv sync --locked --no-dev

# The runtime user needs a home for uv's cache and ownership of the virtual
# environment. UV_NO_SYNC keeps `uv run` from re-resolving inside a built
# image, which would need the network and a writable lock.
RUN useradd --uid 10001 --create-home plimsoll && chown -R 10001:10001 /srv
ENV HOME=/home/plimsoll UV_NO_SYNC=1 UV_FROZEN=1
USER 10001
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "plimsoll_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
