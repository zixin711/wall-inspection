#!/usr/bin/env bash
# 启动服务。首次请先执行 ./setup.sh
#   ./run.sh            HTTP 启动
#   ./run.sh --https    HTTPS 启动（手机上才能用实时取景与对齐残影）
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || { echo "还没有虚拟环境，请先执行 ./setup.sh"; exit 1; }
[ -f .env ] || cp .env.example .env

PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2 | tr -d ' \r')
PORT="${PORT:-8000}"

ip=$( (ipconfig getifaddr en0 2>/dev/null) \
   || (hostname -I 2>/dev/null | awk '{print $1}') \
   || echo 127.0.0.1 )

USE_TLS=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --https) USE_TLS=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

if [ "$USE_TLS" = "1" ]; then
  [ -f certs/cert.pem ] || scripts/make_cert.sh "$ip"
  echo "服务地址：  https://127.0.0.1:$PORT"
  echo "手机访问：  https://$ip:$PORT"
  echo "（自签名证书，手机首次访问会提示不安全，点「继续访问」即可）"
  echo
  exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
       --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem ${ARGS[@]+"${ARGS[@]}"}
fi

echo "服务地址：  http://127.0.0.1:$PORT"
echo "手机访问：  http://$ip:$PORT"
echo "提示：HTTP 下手机浏览器不允许调用摄像头，页面会退回「用相机拍摄」（系统相机，"
echo "      可正常完成检测，只是没有对齐残影）。要用实时取景请改用：./run.sh --https"
echo

exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" ${ARGS[@]+"${ARGS[@]}"}
