#!/command/with-contenv bash
# shellcheck shell=bash
# Shared helpers for gbrain-aio first-boot and s6 services.

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

load_runtime() {
  if [ -f /var/lib/gbrain/runtime.env ]; then
    set -a
    # shellcheck disable=SC1091
    . /var/lib/gbrain/runtime.env
    set +a
  fi
}

source_name() {
  printf '%s' "${SOURCE_NAME:-my-brain}"
}

source_path() {
  printf '/%s' "$(source_name)"
}

config_json() {
  printf '%s' /var/lib/gbrain/.gbrain/config.json
}

is_on() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

wait_postgres() {
  local i
  for i in $(seq 1 60); do
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  log "postgres did not become ready"
  return 1
}

wait_vector() {
  local db i
  db="${POSTGRES_DB:-gbrain}"
  export PGPASSWORD="${POSTGRES_PASSWORD:-}"
  for i in $(seq 1 90); do
    if gosu postgres psql -d "${db}" -tAc "SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null | grep -qx 1; then
      return 0
    fi
    sleep 1
  done
  log "vector extension did not become ready"
  return 1
}

wait_config() {
  local i
  for i in $(seq 1 180); do
    if [ -f "$(config_json)" ]; then
      return 0
    fi
    sleep 1
  done
  log "config.json did not appear"
  return 1
}

gbrain_as() {
  gosu gbrain env HOME=/var/lib/gbrain "$@"
}

normalize_ollama_v1() {
  local base="${1:-}"
  base="${base%/}"
  if [ -z "${base}" ]; then
    return 0
  fi
  case "${base}" in
    */v1) printf '%s' "${base}" ;;
    *) printf '%s/v1' "${base}" ;;
  esac
}
