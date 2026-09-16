#!/command/with-contenv bash
# shellcheck shell=bash
# shellcheck disable=SC2312 # intentional: log() masks return
set -euo pipefail

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# runtime.env (written by 01-bootstrap) carries the derived PGDATA/CERT_DIR/GBRAIN_HOME
if [[ -n ${AIO_APPDATA-} ]]; then
	RT="${AIO_APPDATA}/gbrain-home/runtime.env"
else
	RT="${GBRAIN_HOME:-/var/lib/gbrain}/runtime.env"
fi
if [[ -f ${RT} ]]; then
	set -a
	# shellcheck disable=SC1091
	. "${RT}"
	set +a
fi

PGDATA="${PGDATA:-/data/postgres}"
# runtime.env (written by bootstrap) carries the derived PGDATA when AIO_APPDATA is set
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
