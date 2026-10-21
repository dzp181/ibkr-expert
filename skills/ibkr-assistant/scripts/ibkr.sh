#!/usr/bin/env bash
# IBKR 实操 CLI 包装脚本
# 用法：./scripts/ibkr.sh ping --port 7497
# 用 IBKR_PYTHON 指定解释器（默认 python3，Windows 可设成 python）
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${IBKR_PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: 找不到解释器 $PY，用 IBKR_PYTHON=/path/to/python3 指定" >&2
  exit 3
fi

exec "$PY" "$DIR/ibkr.py" "$@"
