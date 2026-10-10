#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo '请先按 README 创建 .venv 并安装依赖。'
  exit 1
fi
if [ ! -f dist/index.html ]; then
  echo '请先运行 npm ci && npm run build。'
  exit 1
fi
echo 'Web Scan: http://127.0.0.1:8787'
exec .venv/bin/python -m webscan "$@"
