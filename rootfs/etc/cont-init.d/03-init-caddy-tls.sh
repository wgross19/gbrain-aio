#!/command/with-contenv bash
# shellcheck shell=bash
set -euo pipefail

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

CERT_DIR="${CADDY_CERT_DIR:-/config/caddy/certs}"
install -d -m 0755 "${CERT_DIR}"

if [ -s "${CERT_DIR}/cert.pem" ] && [ -s "${CERT_DIR}/key.pem" ]; then
  log "reusing existing Caddy TLS cert"
  exit 0
fi

SAN_IP="${GBRAIN_LAN_BIND:-192.168.1.10}"
log "minting self-signed TLS cert SAN IP:${SAN_IP} DNS:gbrain-vanilla.lan"
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
  -keyout "${CERT_DIR}/key.pem" \
  -out "${CERT_DIR}/cert.pem" \
  -subj "/CN=gbrain-vanilla.lan" \
  -addext "subjectAltName=IP:${SAN_IP},IP:127.0.0.1,DNS:gbrain-vanilla.lan,DNS:localhost"

# Self-signed: the cert is also the CA P4 can copy into Hermes.
cp -a "${CERT_DIR}/cert.pem" "${CERT_DIR}/ca.pem"
chmod 644 "${CERT_DIR}/cert.pem" "${CERT_DIR}/ca.pem"
chmod 600 "${CERT_DIR}/key.pem"
log "wrote ${CERT_DIR}/cert.pem and ${CERT_DIR}/ca.pem"
