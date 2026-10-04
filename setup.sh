#!/usr/bin/env bash
# 创建虚拟环境并安装依赖。可重复执行。
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "找不到 $PY，请先安装 Python 3.10+"; exit 1; }

ver=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
case "$ver" in
  3.1[0-9]|3.[2-9][0-9]) ;;
  *) echo "需要 Python 3.10 及以上，当前是 $ver。可用 PYTHON=python3.12 ./setup.sh 指定版本"; exit 1;;
esac

if [ ! -d .venv ]; then
  echo "创建虚拟环境 .venv（Python $ver）"
  "$PY" -m venv .venv
else
  echo "虚拟环境已存在，跳过创建"
fi

./.venv/bin/python -m pip install --upgrade pip --quiet
echo "安装依赖…"
./.venv/bin/python -m pip install -r requirements.txt --quiet

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env —— 记得改 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL"
fi

echo
echo "完成。启动服务：  ./run.sh"
echo "跑测试：          ./.venv/bin/python scripts/smoke_test.py"
