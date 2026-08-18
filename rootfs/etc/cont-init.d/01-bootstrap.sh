#!/command/with-contenv bash
# shellcheck shell=bash
set -euo pipefail

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

install -d -m 0755 /var/lib/gbrain /data/postgres /run/postgresql /config/caddy/certs
chown -R gbrain:users /var/lib/gbrain
chown -R postgres:postgres /data/postgres /run/postgresql

SOURCE_NAME="${SOURCE_NAME:-cortext-test}"
SOURCE_PATH="/${SOURCE_NAME}"
if [ -d "${SOURCE_PATH}" ]; then
  BRAIN_UID="${BRAIN_UID:-999}"
  BRAIN_GID="${BRAIN_GID:-100}"
  case "${BRAIN_UID}${BRAIN_GID}" in
    *[!0-9]*) log "error: BRAIN_UID/BRAIN_GID must be numeric"; exit 64 ;;
  esac
  chown -R "${BRAIN_UID}:${BRAIN_GID}" "${SOURCE_PATH}" || log "warn: could not chown ${SOURCE_PATH}"
fi

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  log "error: POSTGRES_PASSWORD is required"
  exit 64
fi
case "${POSTGRES_PASSWORD}" in
  *[!A-Za-z0-9]*) log "error: POSTGRES_PASSWORD must be alphanumeric (A-Za-z0-9)"; exit 64 ;;
esac

ENCODED_URL="$(
  POSTGRES_USER="${POSTGRES_USER:-gbrain}" \
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  POSTGRES_DB="${POSTGRES_DB:-gbrain}" \
  GBRAIN_DB_HOST="${GBRAIN_DB_HOST:-127.0.0.1}" \
  GBRAIN_DB_PORT="${GBRAIN_DB_PORT:-5432}" \
  /usr/local/bin/gbrain-encode-url
)"

umask 077
cat > /var/lib/gbrain/runtime.env <<EOF
DATABASE_URL=${ENCODED_URL}
GBRAIN_DATABASE_URL=${ENCODED_URL}
GBRAIN_HOME=/var/lib/gbrain
GBRAIN_HTTP_PORT=${GBRAIN_HTTP_PORT:-3131}
GBRAIN_HTTP_BIND=${GBRAIN_HTTP_BIND:-127.0.0.1}
GBRAIN_PUBLIC_URL=${GBRAIN_PUBLIC_URL:-https://192.168.1.10:3132}
EOF
chown gbrain:users /var/lib/gbrain/runtime.env
chmod 600 /var/lib/gbrain/runtime.env
log "runtime env written (password not logged)"
