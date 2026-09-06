ARG BUN_VERSION=1.3.10
ARG BUN_DIGEST=sha256:b2e30c1564e3e72851df7a78bb8444cd02cca94b3b27275574a97ee99db20c03
ARG BUN_IMAGE=oven/bun:${BUN_VERSION}
ARG CADDY_VERSION=2.10.2
ARG CADDY_DIGEST=sha256:d8c17a862962def15cde69863a3a463f25a2664942eafd7bdbf050e9c3116b83
ARG CADDY_IMAGE=caddy:${CADDY_VERSION}-alpine
ARG DEBIAN_IMAGE=debian:bookworm-slim@sha256:362e64223cc0da95422b3b13c045186fc0a81250e765d31c025fbddf257f6143
ARG S6_OVERLAY_VERSION=3.2.1.0

#checkov:skip=CKV_DOCKER_7:base images are digest-pinned, not 'latest'
FROM ${BUN_IMAGE}@${BUN_DIGEST} AS bun
#checkov:skip=CKV_DOCKER_7:base images are digest-pinned, not 'latest'
FROM ${CADDY_IMAGE}@${CADDY_DIGEST} AS caddy

#checkov:skip=CKV_DOCKER_8:s6 is PID 1 and must start as root; it drops privileges to gbrain (99:100) for runtime services
FROM ${DEBIAN_IMAGE}

ARG GBRAIN_GIT_SHA=5cfb84f1d3a809c70064c292c23db3d538d5c551
ARG GBRAIN_REPO=https://github.com/garrytan/gbrain.git
ARG S6_OVERLAY_VERSION=3.2.1.0
ARG POSTGRES_MAJOR=17
# Upstream GBrain release version. The fleet reads this ARG (via
# version_key: UPSTREAM_VERSION) to compute the release tag. The build stays
# pinned to GBRAIN_GIT_SHA; this is the discoverable release label only.
ARG UPSTREAM_VERSION=v0.48.2.0

LABEL org.opencontainers.image.title="gbrain-aio" \
      org.opencontainers.image.source="${GBRAIN_REPO}" \
      org.opencontainers.image.revision="${GBRAIN_GIT_SHA}" \
      com.gbrain-service.component="gbrain-aio"

ENV DEBIAN_FRONTEND=noninteractive \
    GBRAIN_HOME=/var/lib/gbrain \
    PATH="/usr/lib/postgresql/17/bin:/usr/local/bin:${PATH}" \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=300000 \
    S6_KEEP_ENV=1

USER root
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    openssl \
    xz-utils \
    gosu \
  && install -d -m 0755 /etc/apt/keyrings \
  && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg \
  && echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    postgresql-${POSTGRES_MAJOR} \
    postgresql-${POSTGRES_MAJOR}-pgvector \
    postgresql-client-${POSTGRES_MAJOR} \
  && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" -o /tmp/s6-noarch.tar.xz \
  && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz" -o /tmp/s6-arch.tar.xz \
  && tar -C / -Jxpf /tmp/s6-noarch.tar.xz \
  && tar -C / -Jxpf /tmp/s6-arch.tar.xz \
  && rm -f /tmp/s6-noarch.tar.xz /tmp/s6-arch.tar.xz \
  && useradd --system --uid 99 --gid users --home-dir /var/lib/gbrain --create-home gbrain \
  && mkdir -p /opt/gbrain /var/lib/gbrain /data/postgres /run/postgresql /config/caddy/certs \
  && chown -R gbrain:users /opt/gbrain /var/lib/gbrain \
  && chown -R postgres:postgres /data/postgres /run/postgresql \
  && rm -rf /var/lib/apt/lists/*

COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy

WORKDIR /opt/gbrain
RUN git clone --filter=blob:none "${GBRAIN_REPO}" /tmp/gbrain-src \
  && git -C /tmp/gbrain-src checkout --detach "${GBRAIN_GIT_SHA}" \
  && test "$(git -C /tmp/gbrain-src rev-parse HEAD)" = "${GBRAIN_GIT_SHA}" \
  && test -f /tmp/gbrain-src/admin/dist/index.html \
  && cp -a /tmp/gbrain-src/. /opt/gbrain/ \
  && rm -rf /tmp/gbrain-src \
  && bun install --frozen-lockfile \
  && chown -R gbrain:users /opt/gbrain \
  && printf '%s\n' '#!/bin/sh' 'if [ -f /var/lib/gbrain/runtime.env ]; then set -a; . /var/lib/gbrain/runtime.env; set +a; fi' 'exec bun run /opt/gbrain/src/cli.ts "$@"' > /usr/local/bin/gbrain \
  && chmod 755 /usr/local/bin/gbrain

COPY rootfs/ /
RUN chmod 755 /etc/cont-init.d/* /etc/services.d/*/run /usr/local/bin/* /usr/local/lib/gbrain-aio-lib.sh

EXPOSE 3132
VOLUME ["/data/postgres", "/var/lib/gbrain", "/config/caddy"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=10 \
  CMD curl -fsS http://127.0.0.1:3131/health || exit 1

ENTRYPOINT ["/init"]
