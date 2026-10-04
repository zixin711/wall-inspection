#!/usr/bin/env bash
# Generate a self-signed certificate for mobile browser camera access.
# Browsers allow getUserMedia only over HTTPS or localhost. A phone accessing
# a local IP over HTTP cannot use live preview or the alignment overlay.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p certs
ip="${1:-$( (ipconfig getifaddr en0 2>/dev/null) || (hostname -I 2>/dev/null | awk '{print $1}') || echo 127.0.0.1 )}"

command -v openssl >/dev/null || { echo "Cannot find openssl"; exit 1; }

cat > certs/openssl.cnf <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = mould-inspect
[ext]
subjectAltName = DNS:localhost, IP:127.0.0.1, IP:${ip}
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
CNF

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout certs/key.pem -out certs/cert.pem -config certs/openssl.cnf >/dev/null 2>&1

echo "Certificate generated: certs/cert.pem (valid for 825 days; SAN includes ${ip})"
echo "A browser may warn about this self-signed certificate on first access."
echo "If the local IP changes, rerun: scripts/make_cert.sh <new-ip>"
