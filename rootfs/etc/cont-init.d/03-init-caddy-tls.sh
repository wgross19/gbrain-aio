#!/command/with-contenv bash
# shellcheck shell=bash
# shellcheck disable=SC2312  # intentional: log() masks the SAN capture return
# Mint the self-signed TLS cert. Idempotent: re-mints only when the existing
# cert's SAN does not yet cover the required entries (LAN IP, extras, tailnet).
set -euo pipefail

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# Bootstrap (01-bootstrap.sh) has already written runtime.env by the time this
# cont-init runs; source it FIRST so the derived CERT_DIR (single appdata root)
# and LAN_BIND/extras are visible. The literal fallback preserves the legacy
# split layout for standalone installs without AIO_APPDATA.
if [ -n "${AIO_APPDATA:-}" ]; then
	RT="${AIO_APPDATA}/gbrain-home/runtime.env"
else
	RT="${GBRAIN_HOME:-/var/lib/gbrain}/runtime.env"
fi
if [[ -f "${RT}" ]]; then
	set -a
	# shellcheck disable=SC1091
	. "${RT}"
	set +a
fi
CERT_DIR="${CERT_DIR:-/config/caddy/certs}"

SAN_IP="${GBRAIN_LAN_BIND-}"
if [[ -z ${SAN_IP} ]]; then
	log "error: GBRAIN_LAN_BIND is required to mint the TLS cert"
	exit 64
fi

# Base SAN plus optional extras (CERT_EXTRA_DNS / CERT_EXTRA_IPS, comma-sep).
# With Unraid's per-container Tailscale integration, bootstrap derives the
# MagicDNS name + 100.x IP automatically and adds them here; these vars are
# the manual fallback.
EXTRA_PARTS=""
for dns in $(printf '%s' "${CERT_EXTRA_DNS-}" | tr ',' ' '); do
	[[ -n ${dns} ]] && EXTRA_PARTS="${EXTRA_PARTS},DNS:${dns}"
done
for ip in $(printf '%s' "${CERT_EXTRA_IPS-}" | tr ',' ' '); do
	[[ -n ${ip} ]] && EXTRA_PARTS="${EXTRA_PARTS},IP:${ip}"
done
# The tailnet MagicDNS name + IP bootstrap discovered this boot.
TS_NAME="${TS_DERIVED_DNS_NAME-}"
TS_IP="${TS_DERIVED_IP-}"
if [[ -n ${TS_NAME} ]]; then
	EXTRA_PARTS="${EXTRA_PARTS},DNS:${TS_NAME}"
fi
if [[ -n ${TS_IP} ]]; then
	EXTRA_PARTS="${EXTRA_PARTS},IP:${TS_IP}"
fi

needs_mint() {
	[[ ! -s "${CERT_DIR}/cert.pem" || ! -s "${CERT_DIR}/key.pem" ]] && return 0
	local san
	# Normalize: 'IP Address:x' renders as 'IPAddress:x' after whitespace strip.
	san="$(openssl x509 -in "${CERT_DIR}/cert.pem" -noout -text 2>/dev/null |
		tr -d ' ' | grep -iE 'IPAddress|DNS:' | tr '\n' ' ' || true)"
	[[ -n ${SAN_IP} && ${san} != *"IPAddress:${SAN_IP}"* ]] && return 0
	for dns in $(printf '%s' "${CERT_EXTRA_DNS-}" | tr ',' ' '); do
		[[ -n ${dns} ]] && [[ ${san} != *"DNS:${dns}"* ]] && return 0
	done
	for ip in $(printf '%s' "${CERT_EXTRA_IPS-}" | tr ',' ' '); do
		[[ -n ${ip} ]] && [[ ${san} != *"IPAddress:${ip}"* ]] && return 0
	done
	return 1
}

NEEDS_MINT=0
# shellcheck disable=SC2310  # intentional: capture predicate exit code without set -e abort
needs_mint || NEEDS_MINT=$?
if [[ ${NEEDS_MINT} -ne 0 ]]; then
	log "TLS cert covers all required SAN entries"
	exit 0
fi

log "minting self-signed TLS cert SAN IP:${SAN_IP} DNS:gbrain-aio.lan${EXTRA_PARTS}"
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
	-keyout "${CERT_DIR}/key.pem" \
	-out "${CERT_DIR}/cert.pem" \
	-subj "/CN=gbrain-aio.lan" \
	-addext "subjectAltName=IP:${SAN_IP},IP:127.0.0.1,DNS:gbrain-aio.lan,DNS:localhost${EXTRA_PARTS}"

# Self-signed: the cert is also the CA P4 can copy into Hermes.
cp -a "${CERT_DIR}/cert.pem" "${CERT_DIR}/ca.pem"
chmod 644 "${CERT_DIR}/cert.pem" "${CERT_DIR}/ca.pem"
chmod 600 "${CERT_DIR}/key.pem"
log "wrote ${CERT_DIR}/cert.pem and ${CERT_DIR}/ca.pem"
