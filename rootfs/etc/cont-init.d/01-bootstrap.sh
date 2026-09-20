#!/command/with-contenv bash
# shellcheck shell=bash
# shellcheck disable=SC2312,SC2249 # intentional: log() masks return; case has no default
set -euo pipefail

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# Option B: fixed mount target + neutral source id (source_path/source_name
# in the lib are the single source of truth for downstream consumers).
SOURCE_PATH="/source/brain"
# --- Single appdata root: AIO_APPDATA (container path of the one host mount) --
# Defaults preserve the legacy split layout when AIO_APPDATA is not set.
AIO_APPDATA="${AIO_APPDATA-}"
if [[ -n ${AIO_APPDATA} ]]; then
	POSTGRES_DATA="${AIO_APPDATA}/data/postgres"
	GBRAIN_HOME_DIR="${AIO_APPDATA}/gbrain-home"
	CADDY_CERTS="${AIO_APPDATA}/caddy"
	# One-time data adoption: an existing install upgrading to the single-root
	# layout keeps its data. Copy only when the legacy location has content and
	# the new location does not (idempotent; never overwrites live data).
	if [[ -d /var/lib/gbrain && ! -d ${GBRAIN_HOME_DIR}/.gbrain && -d /var/lib/gbrain/.gbrain ]]; then
		log "migrating gbrain-home -> ${GBRAIN_HOME_DIR}"
		mkdir -p "${GBRAIN_HOME_DIR}"
		if ! cp -a /var/lib/gbrain/. "${GBRAIN_HOME_DIR}/"; then
			log "warn: gbrain-home migration incomplete"
		fi
	fi
	if [[ -d /data/postgres && ! -s ${POSTGRES_DATA}/PG_VERSION ]]; then
		log "migrating postgres data -> ${POSTGRES_DATA}"
		mkdir -p "${POSTGRES_DATA}"
		if ! cp -a /data/postgres/. "${POSTGRES_DATA}/"; then
			log "warn: postgres migration incomplete"
		fi
	fi
	if [[ -d /config/caddy/certs && ! -s ${CADDY_CERTS}/certs/cert.pem ]]; then
		log "migrating caddy certs -> ${CADDY_CERTS}"
		mkdir -p "${CADDY_CERTS}"
		if ! cp -a /config/caddy/. "${CADDY_CERTS}/"; then
			log "warn: caddy migration incomplete"
		fi
	fi
else
	POSTGRES_DATA="/data/postgres"
	GBRAIN_HOME_DIR="/var/lib/gbrain"
	CADDY_CERTS="/config/caddy"
fi

install -d -m 0755 "${GBRAIN_HOME_DIR}" "${POSTGRES_DATA}" /run/postgresql "${CADDY_CERTS}/certs"
chown -R postgres:postgres "${POSTGRES_DATA}" /run/postgresql
chown -R gbrain:users "${GBRAIN_HOME_DIR}"
if [[ -d ${SOURCE_PATH} ]]; then
	BRAIN_UID="${BRAIN_UID:-99}"
	BRAIN_GID="${BRAIN_GID:-100}"
	case "${BRAIN_UID}${BRAIN_GID}" in
	*[!0-9]*)
		log "error: BRAIN_UID/BRAIN_GID must be numeric"
		exit 64
		;;
	esac
	chown -R "${BRAIN_UID}:${BRAIN_GID}" "${SOURCE_PATH}" || log "warn: could not chown ${SOURCE_PATH}"
fi

if [[ -z ${POSTGRES_PASSWORD-} ]]; then
	log "error: POSTGRES_PASSWORD is required"
	exit 64
fi
case "${POSTGRES_PASSWORD}" in
*[!A-Za-z0-9]*)
	log "error: POSTGRES_PASSWORD must be alphanumeric (A-Za-z0-9)"
	exit 64
	;;
esac

ENCODED_URL="$(
	POSTGRES_USER="${POSTGRES_USER:-gbrain}" \
		POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
		POSTGRES_DB="${POSTGRES_DB:-gbrain}" \
		GBRAIN_DB_HOST="${GBRAIN_DB_HOST:-127.0.0.1}" \
		GBRAIN_DB_PORT="${GBRAIN_DB_PORT:-5432}" \
		/usr/local/bin/gbrain-encode-url
)"

# --- Single origin: PUBLIC_URL derived from LAN_BIND unless overridden ------
# LAN_BIND is the only required network input; it also feeds the cert SAN.
if [[ -z ${GBRAIN_LAN_BIND-} ]]; then
	log "error: GBRAIN_LAN_BIND is required (Unraid host LAN IP)"
	exit 64
fi
case "${GBRAIN_LAN_BIND}" in
*[!0-9.]*)
	log "error: GBRAIN_LAN_BIND must be an IPv4 address"
	exit 64
	;;
esac
PUBLIC_URL="${GBRAIN_PUBLIC_URL-}"
if [[ -z ${PUBLIC_URL} ]]; then
	# Tailnet auto-derivation (TS_PUBLIC_URL=auto|off|<url>):
	#   auto (default when a tailscaled socket exists): use the MagicDNS name.
	#   off: keep the LAN origin. <url>: explicit origin wins.
	TS_SOCKET="${TS_SOCKET:-/var/run/tailscale/tailscaled.sock}"
	TS_POLICY="${TS_PUBLIC_URL-}"
	if [[ -S ${TS_SOCKET} ]]; then
		TS_POLICY="${TS_POLICY:-auto}"
	fi
	TS_NAME=""
	TS_IP=""
	if [[ ${TS_POLICY} == auto ]]; then
		for _ in $(seq 1 30); do
			TS_JSON="$(tailscale status --json 2>/dev/null || true)"
			TS_NAME="$(printf '%s' "${TS_JSON}" | sed -n 's/.*"DNSName": *"\([^"]*\)".*/\1/p' | head -1 || true)"
			[[ -n ${TS_NAME} ]] && break
			sleep 1
		done
		TS_IP="$(printf '%s' "${TS_JSON-}" | sed -n 's/.*"TailscaleIPs": *\[\s*"\([^"]*\)".*/\1/p' | head -1 || true)"
		if [[ -n ${TS_NAME} ]]; then
			PUBLIC_URL="https://${TS_NAME}:${GBRAIN_HTTP_PORT:-3132}"
			log "tailscale detected; public URL derived from MagicDNS name"
		fi
	elif [[ ${TS_POLICY} != off && -n ${TS_POLICY} ]]; then
		PUBLIC_URL="${TS_POLICY}"
	fi
	if [[ -z ${PUBLIC_URL} ]]; then
		PUBLIC_URL="https://${GBRAIN_LAN_BIND}:${GBRAIN_HTTP_PORT:-3132}"
	fi
fi
export TS_DERIVED_DNS_NAME="${TS_NAME-}"
export TS_DERIVED_IP="${TS_IP-}"

TOGETHER_KEY="${TOGETHER_API_KEY-}"
if [[ -n ${OLLAMA_BASE_URL-} && -z ${TOGETHER_KEY} ]]; then
	# The ollama recipe requires no key; this placeholder only satisfies the
	# gateway's together-recipe key check in legacy CHAT_PROVIDER=together mode.
	TOGETHER_KEY=ollama
fi

umask 077
cat >"${GBRAIN_HOME_DIR}/runtime.env" <<EOF
DATABASE_URL=${ENCODED_URL}
GBRAIN_DATABASE_URL=${ENCODED_URL}
GBRAIN_HOME=${GBRAIN_HOME_DIR}
PGDATA=${POSTGRES_DATA}
CERT_DIR=${CADDY_CERTS}/certs
AIO_APPDATA=${AIO_APPDATA-}
GBRAIN_HTTP_PORT=${GBRAIN_HTTP_PORT:-3131}
GBRAIN_HTTP_BIND=${GBRAIN_HTTP_BIND:-127.0.0.1}
GBRAIN_LAN_BIND=${GBRAIN_LAN_BIND}
OLLAMA_API_KEY=${OLLAMA_API_KEY-}
GBRAIN_PUBLIC_URL=${PUBLIC_URL}
CHAT_PROVIDER=${CHAT_PROVIDER-}
CHAT_MODEL=${CHAT_MODEL:-deepseek-v4-flash:cloud}
TOGETHER_API_KEY=${TOGETHER_KEY}
EMBEDDING_MODEL=${EMBEDDING_MODEL-}
EMBEDDING_DIMENSIONS=${EMBEDDING_DIMENSIONS-}
SCHEMA_PACK=${SCHEMA_PACK-}
AUTOPILOT_INTERVAL=${AUTOPILOT_INTERVAL:-1800}
DREAM_AT=${DREAM_AT:-02:00}
DOCTOR_DAY=${DOCTOR_DAY:-monday}
DOCTOR_AT=${DOCTOR_AT:-06:00}
TS_SOCKET=${TS_SOCKET:-/var/run/tailscale/tailscaled.sock}
TS_DERIVED_DNS_NAME=${TS_DERIVED_DNS_NAME-}
TS_DERIVED_IP=${TS_DERIVED_IP-}
CERT_EXTRA_DNS=${CERT_EXTRA_DNS-}
CERT_EXTRA_IPS=${CERT_EXTRA_IPS-}
GBRAIN_EXTRA_CONFIG=${GBRAIN_EXTRA_CONFIG-}
EOF

# --- GBRAIN_EXTRA_ENV: env-only passthrough (closes the env-only gap) -------
# Comma-separated KEY=VALUE pairs appended to runtime.env. Allowlist-only:
# safety-critical gates stay out of reach of the catch-all.
EXTRA_ENV="${GBRAIN_EXTRA_ENV-}"
if [[ -n ${EXTRA_ENV} ]]; then
	IFS=',' read -ra _PAIRS <<<"${EXTRA_ENV}"
	for pair in "${_PAIRS[@]}"; do
		pair="${pair#"${pair%%[![:space:]]*}"}"
		pair="${pair%"${pair##*[![:space:]]}"}"
		[[ -z ${pair} ]] && continue
		KEY="${pair%%=*}"
		case "${KEY}" in
		GBRAIN_EMBEDDING_MULTIMODAL | GBRAIN_EMBEDDING_MULTIMODAL_MODEL | \
			GBRAIN_EMBEDDING_IMAGE_OCR | GBRAIN_EMBEDDING_IMAGE_OCR_MODEL | \
			GBRAIN_SEARCH_EXCLUDE | GBRAIN_SOURCE_BOOST | GBRAIN_CHAT_FALLBACK_CHAIN | \
			GBRAIN_BACKUP_CHECK | GBRAIN_BACKUP_CHECK_DAYS | GBRAIN_AUTOPILOT_LABEL | \
			GBRAIN_TRAJECTORY_REGRESSION_THRESHOLD | GBRAIN_EMBED_CONCURRENCY | \
			GBRAIN_RETRIEVAL_REFLEX_VOLUNTEER | GBRAIN_RETRIEVAL_REFLEX_WINDOW_TURNS) ;;
		*)
			log "error: GBRAIN_EXTRA_ENV refused '${KEY}' (not on the allowlist)"
			exit 64
			;;
		esac
		if printf '%s' "${pair}" | grep -q '[[:space:]]'; then
			log "error: GBRAIN_EXTRA_ENV refused (contains whitespace): ${KEY}"
			exit 64
		fi
		printf '%s\n' "${pair}" >>"${GBRAIN_HOME_DIR}/runtime.env"
	done
fi
chown gbrain:users "${GBRAIN_HOME_DIR}/runtime.env"
chmod 600 "${GBRAIN_HOME_DIR}/runtime.env"
log "runtime env written (password not logged)"
