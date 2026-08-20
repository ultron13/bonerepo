# The generator image: a JRE, JMeter, and the agent. Nothing here downloads
# anything at run time -- an air-gapped deployment is a stated v1.0 goal and is
# cheap to keep, expensive to recover.

# JMeter is fetched once, at build time, and verified. The checksum is not
# decoration: it is the only thing standing between a mirror compromise and
# arbitrary code inside every generator. Re-derive it with
#   curl -fsSL https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz.sha512
# If this check fails, stop and find out why. Relaxing it turns the control
# into a comment.
FROM eclipse-temurin:21-jre-noble AS jmeter
ARG JMETER_VERSION=5.6.3
ARG JMETER_SHA512=5978a1a35edb5a7d428e270564ff49d2b1b257a65e17a759d259a9283fc17093e522fe46f474a043864aea6910683486340706d745fcdf3db1505fd71e689083
# dlcdn first, archive second. dlcdn is the fast mirror but carries only
# current releases; archive.apache.org is the permanent home but is rate
# limited to roughly 100 KB/s, which puts a quarter of an hour into every clean
# `make dev`. Which host answered does not establish trust -- the checksum
# below does -- so preferring the fast one costs nothing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && for mirror in https://dlcdn.apache.org/jmeter/binaries \
                     https://archive.apache.org/dist/jmeter/binaries; do \
         curl -fsSL --max-time 900 -o /tmp/jmeter.tgz \
           "$mirror/apache-jmeter-${JMETER_VERSION}.tgz" && break; \
       done \
    && echo "${JMETER_SHA512}  /tmp/jmeter.tgz" | sha512sum -c - \
    && mkdir -p /opt/jmeter \
    && tar -xzf /tmp/jmeter.tgz -C /opt/jmeter --strip-components=1 \
    && rm /tmp/jmeter.tgz \
    # The GUI is never started, and shipping it enlarges the image and the
    # attack surface for no benefit.
    && rm -rf /opt/jmeter/docs /opt/jmeter/printable_docs

# Noble rather than jammy: the agent needs Python 3.12, which 22.04 does not
# carry and 24.04 does.
FROM eclipse-temurin:21-jre-noble AS base
ENV PYTHONUNBUFFERED=1
COPY --from=jmeter /opt/jmeter /opt/jmeter

# No git: plans arrive as a staged bundle, and a git binary here would invite
# the credential that staging exists to avoid.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3.12-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
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
ENV HOME=/home/plimsoll UV_NO_SYNC=1 UV_FROZEN=1 \
    PLIMSOLL_JMETER_BINARY=/opt/jmeter/bin/jmeter
USER 10001
CMD ["uv", "run", "python", "-m", "plimsoll_agent"]
