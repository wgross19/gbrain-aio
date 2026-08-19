#!/command/with-contenv bash
# shellcheck shell=bash
# shellcheck disable=SC2312 # intentional: log() masks return
set -euo pipefail

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

PGDATA="${PGDATA:-/data/postgres}"
install -d -m 0700 "${PGDATA}"
install -d -m 2775 /run/postgresql
chown -R postgres:postgres "${PGDATA}" /run/postgresql

if [[ ! -s "${PGDATA}/PG_VERSION" ]]; then
	log "initializing empty PostgreSQL data dir"
	gosu postgres /usr/lib/postgresql/17/bin/initdb -D "${PGDATA}" --auth-local=peer --auth-host=scram-sha-256 --username=postgres
fi

install -d -m 0755 "${PGDATA}/conf.d"
cat >"${PGDATA}/conf.d/aio.conf" <<'EOF'
listen_addresses = '127.0.0.1'
port = 5432
unix_socket_directories = '/run/postgresql'
EOF
chown -R postgres:postgres "${PGDATA}/conf.d"

if ! grep -q "include_dir = 'conf.d'" "${PGDATA}/postgresql.conf"; then
	printf "\ninclude_dir = 'conf.d'\n" >>"${PGDATA}/postgresql.conf"
fi

log "postgres data dir ready"
