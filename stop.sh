#!/usr/bin/env bash
# 停止 wx-insights：schedule 守护进程 + PostgreSQL (Docker)
#
# 在 worker 的上一级目录执行：
#   worker/stop.sh

set -euo pipefail

WORKER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${WORKER_ROOT}/data/schedule-daemon.pid"

# ── schedule ──
if [[ -f "${PID_FILE}" ]]; then
  pid="$(cat "${PID_FILE}")"
  if kill -0 "${pid}" 2>/dev/null && ps -p "${pid}" -o command= | grep -qF "python -m worker schedule"; then
    echo ">>> 停止 schedule (PID ${pid})..."
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "${pid}" 2>/dev/null; then
      echo ">>> 强制结束 schedule..."
      kill -9 "${pid}" 2>/dev/null || true
    fi
  else
    echo ">>> schedule 未在运行"
  fi
  rm -f "${PID_FILE}"
else
  echo ">>> schedule 未在运行"
fi

# ── PostgreSQL ──
if command -v docker >/dev/null 2>&1; then
  echo ">>> 停止 PostgreSQL..."
  (cd "${WORKER_ROOT}" && docker compose down)
else
  echo ">>> 跳过数据库：未安装 docker"
fi

echo ">>> 已停止"
