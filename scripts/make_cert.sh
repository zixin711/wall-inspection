#!/usr/bin/env bash
# 生成自签名证书，让手机浏览器允许调用摄像头。
# 浏览器只在安全上下文（HTTPS 或 localhost）下开放 getUserMedia，
# 手机连内网 IP 走的是 HTTP，所以实时取景和对齐残影都用不了。
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p certs
ip="${1:-$( (ipconfig getifaddr en0 2>/dev/null) || (hostname -I 2>/dev/null | awk '{print $1}') || echo 127.0.0.1 )}"

command -v openssl >/dev/null || { echo "找不到 openssl"; exit 1; }

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

echo "证书已生成：certs/cert.pem（有效期 825 天，SAN 含 ${ip}）"
echo "手机首次访问会提示「不安全」——自签名证书正常现象，点继续访问即可。"
echo "换了 Wi-Fi / IP 变了就重新跑一次：scripts/make_cert.sh <新IP>"
