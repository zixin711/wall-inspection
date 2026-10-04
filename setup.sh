#!/usr/bin/env bash
# Create a virtual environment and install dependencies. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "Cannot find $PY; install Python 3.10 or later."; exit 1; }

ver=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
case "$ver" in
  3.1[0-9]|3.[2-9][0-9]) ;;
  *) echo "Python 3.10 or later is required; found $ver. Select another version with PYTHON=python3.12 ./setup.sh"; exit 1;;
esac

if [ ! -d .venv ]; then
  echo "Creating .venv with Python $ver"
  "$PY" -m venv .venv
else
  echo "Virtual environment already exists; skipping creation"
fi

./.venv/bin/python -m pip install --upgrade pip --quiet
echo "Installing dependencies..."
./.venv/bin/python -m pip install -r requirements.txt --quiet

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env; set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL"
fi

echo
echo "Done. Start the service:  ./run.sh"
echo "Run checks:              ./.venv/bin/python scripts/smoke_test.py"
