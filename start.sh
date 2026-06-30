#!/usr/bin/env bash
# 启动 wx-insights：PostgreSQL (Docker) + schedule 守护进程
#
# 在 worker 的上一级目录执行：
#   worker/start.sh

set -euo pipefail

WORKER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${WORKER_ROOT}/.." && pwd)"
PID_FILE="${WORKER_ROOT}/data/schedule-daemon.pid"
LOG_FILE="${WORKER_ROOT}/data/logs/schedule-daemon.log"

mkdir -p "${WORKER_ROOT}/data/logs"

die() { echo "错误: $*" >&2; exit 1; }

PY="${WX_INSIGHTS_PYTHON:-python3}"
command -v "${PY}" >/dev/null 2>&1 || PY=python

# ── PostgreSQL ──
command -v docker >/dev/null 2>&1 || die "未找到 docker"
echo ">>> 启动 PostgreSQL..."
(cd "${WORKER_ROOT}" && docker compose up -d)
echo ">>> 数据库已就绪 (localhost:5432)"

# ── schedule ──
if [[ -f "${PID_FILE}" ]]; then
  pid="$(cat "${PID_FILE}")"
  if kill -0 "${pid}" 2>/dev/null && ps -p "${pid}" -o command= | grep -qF "python -m worker schedule"; then
    echo ">>> schedule 已在运行 (PID ${pid})"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

[[ -f "${PROJECT_ROOT}/worker/__main__.py" ]] || die "未找到 ${PROJECT_ROOT}/worker，请在 worker 的上一级目录执行本脚本"

echo ">>> 启动 schedule..."
echo "    目录: ${PROJECT_ROOT}"
echo "    日志: ${LOG_FILE}"
(
  cd "${PROJECT_ROOT}"
  nohup "${PY}" -m worker schedule >>"${LOG_FILE}" 2>&1 &
  echo $! >"${PID_FILE}"
)
sleep 1

pid="$(cat "${PID_FILE}")"
kill -0 "${pid}" 2>/dev/null || die "schedule 启动失败，查看 ${LOG_FILE}"
echo ">>> schedule 已启动 (PID ${pid})"
