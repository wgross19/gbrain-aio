#!/command/with-contenv bash
# shellcheck shell=bash
# shellcheck disable=SC2312,SC2249 # intentional: log() masks return; case has no default
set -euo pipefail

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

install -d -m 0755 /var/lib/gbrain /data/postgres /run/postgresql /config/caddy/certs
chown -R gbrain:users /var/lib/gbrain
chown -R postgres:postgres /data/postgres /run/postgresql

SOURCE_NAME="${SOURCE_NAME:-my-brain}"
SOURCE_PATH="/${SOURCE_NAME}"
if [[ -d ${SOURCE_PATH} ]]; then
	BRAIN_UID="${BRAIN_UID:-999}"
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
cat >/var/lib/gbrain/runtime.env <<EOF
DATABASE_URL=${ENCODED_URL}
GBRAIN_DATABASE_URL=${ENCODED_URL}
GBRAIN_HOME=/var/lib/gbrain
GBRAIN_HTTP_PORT=${GBRAIN_HTTP_PORT:-3131}
GBRAIN_HTTP_BIND=${GBRAIN_HTTP_BIND:-127.0.0.1}
GBRAIN_LAN_BIND=${GBRAIN_LAN_BIND}
OLLAMA_API_KEY=${OLLAMA_API_KEY-}
GBRAIN_PUBLIC_URL=${PUBLIC_URL}
SOURCE_NAME=${SOURCE_NAME}
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
chown gbrain:users /var/lib/gbrain/runtime.env
chmod 600 /var/lib/gbrain/runtime.env
log "runtime env written (password not logged)"
