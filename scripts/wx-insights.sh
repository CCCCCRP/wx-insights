#!/usr/bin/env bash
# wx-insights 启停：PostgreSQL (Docker) + schedule 守护进程
#
# 用法（在 worker 目录下）：
#   ./scripts/wx-insights.sh start|stop|restart|status|logs
#
# 或在项目根目录（worker 的上一级）：
#   worker/scripts/wx-insights.sh start

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${WORKER_ROOT}/.." && pwd)"
PID_FILE="${WORKER_ROOT}/data/schedule-daemon.pid"
LOG_FILE="${WORKER_ROOT}/data/logs/schedule-daemon.log"
SCHEDULE_PATTERN="python -m worker schedule"

mkdir -p "${WORKER_ROOT}/data/logs"

die() {
  echo "错误: $*" >&2
  exit 1
}

resolve_python() {
  if [[ -n "${WX_INSIGHTS_PYTHON:-}" ]]; then
    echo "${WX_INSIGHTS_PYTHON}"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo python3
    return
  fi
  echo python
}

schedule_running() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  ps -p "${pid}" -o command= 2>/dev/null | grep -qF "${SCHEDULE_PATTERN}"
}

start_db() {
  if ! command -v docker >/dev/null 2>&1; then
    die "未找到 docker，请先安装 Docker"
  fi
  echo ">>> 启动 PostgreSQL (docker compose)..."
  (cd "${WORKER_ROOT}" && docker compose up -d)
  echo ">>> 数据库已启动 (localhost:5432)"
}

stop_db() {
  if ! command -v docker >/dev/null 2>&1; then
    echo ">>> 跳过数据库：未安装 docker"
    return 0
  fi
  echo ">>> 停止 PostgreSQL..."
  (cd "${WORKER_ROOT}" && docker compose down)
}

start_schedule() {
  if schedule_running; then
    echo ">>> schedule 已在运行 (PID $(cat "${PID_FILE}"))"
    return 0
  fi
  rm -f "${PID_FILE}"
  local py
  py="$(resolve_python)"
  if [[ ! -d "${PROJECT_ROOT}/worker" ]] && [[ ! -f "${PROJECT_ROOT}/worker/__main__.py" ]]; then
    # 仓库即 worker/ 且从 worker 内调用时，PROJECT_ROOT 可能不对
    if [[ -f "${WORKER_ROOT}/__main__.py" ]]; then
      PROJECT_ROOT="${WORKER_ROOT}/.."
    fi
  fi
  if [[ ! -f "${PROJECT_ROOT}/worker/__main__.py" ]] && [[ -f "${WORKER_ROOT}/__main__.py" ]]; then
    PROJECT_ROOT="$(dirname "${WORKER_ROOT}")"
  fi
  echo ">>> 启动 schedule 守护进程..."
  echo "    工作目录: ${PROJECT_ROOT}"
  echo "    日志: ${LOG_FILE}"
  (
    cd "${PROJECT_ROOT}"
    nohup "${py}" -m worker schedule >>"${LOG_FILE}" 2>&1 &
    echo $! >"${PID_FILE}"
  )
  sleep 1
  if schedule_running; then
    echo ">>> schedule 已启动 (PID $(cat "${PID_FILE}"))"
  else
    rm -f "${PID_FILE}"
    die "schedule 启动失败，请查看 ${LOG_FILE}"
  fi
}

stop_schedule() {
  if ! schedule_running; then
    rm -f "${PID_FILE}"
    echo ">>> schedule 未在运行"
    return 0
  fi
  local pid
  pid="$(cat "${PID_FILE}")"
  echo ">>> 停止 schedule (PID ${pid})..."
  kill "${pid}" 2>/dev/null || true
  for _ in $(seq 1 10); do
    schedule_running || break
    sleep 1
  done
  if schedule_running; then
    echo ">>> 强制结束 schedule..."
    kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
  echo ">>> schedule 已停止"
}

cmd_start() {
  local what="${1:-all}"
  case "${what}" in
    all)
      start_db
      start_schedule
      ;;
    db)
      start_db
      ;;
    schedule)
      start_schedule
      ;;
    *)
      die "未知 start 目标: ${what}（可用 all | db | schedule）"
      ;;
  esac
}

cmd_stop() {
  local what="${1:-all}"
  case "${what}" in
    all)
      stop_schedule
      stop_db
      ;;
    db)
      stop_db
      ;;
    schedule)
      stop_schedule
      ;;
    *)
      die "未知 stop 目标: ${what}（可用 all | db | schedule）"
      ;;
  esac
}

cmd_status() {
  echo "=== wx-insights 状态 ==="
  echo "worker: ${WORKER_ROOT}"
  echo "project: ${PROJECT_ROOT}"
  echo
  echo "--- PostgreSQL ---"
  if command -v docker >/dev/null 2>&1; then
    (cd "${WORKER_ROOT}" && docker compose ps) || true
  else
    echo "docker 未安装"
  fi
  echo
  echo "--- schedule ---"
  if schedule_running; then
    echo "运行中, PID $(cat "${PID_FILE}")"
    echo "日志: ${LOG_FILE}"
  else
    echo "未运行"
    rm -f "${PID_FILE}"
  fi
  echo
  echo "--- Ollama (embedding) ---"
  if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "localhost:11434 可访问"
  else
    echo "未检测到 Ollama（需本机单独启动: ollama serve / brew services start ollama）"
  fi
}

cmd_logs() {
  touch "${LOG_FILE}"
  tail -n "${1:-50}" -f "${LOG_FILE}"
}

usage() {
  cat <<EOF
用法: $(basename "$0") <command> [options]

命令:
  start [all|db|schedule]   启动服务（默认 all = 数据库 + schedule）
  stop  [all|db|schedule]   停止服务（默认 all）
  restart [all|db|schedule] 重启
  status                    查看状态
  logs [行数]               跟踪 schedule 日志（默认 50 行，-f）

示例:
  $(basename "$0") start
  $(basename "$0") stop schedule
  $(basename "$0") status
  $(basename "$0") logs 100

环境变量:
  WX_INSIGHTS_PYTHON   指定 Python 解释器（默认 python3）
EOF
}

main() {
  local cmd="${1:-}"
  shift || true
  case "${cmd}" in
    start) cmd_start "${1:-all}" ;;
    stop) cmd_stop "${1:-all}" ;;
    restart)
      cmd_stop "${1:-all}"
      cmd_start "${1:-all}"
      ;;
    status) cmd_status ;;
    logs) cmd_logs "${1:-50}" ;;
    -h|--help|help|"") usage ;;
    *) die "未知命令: ${cmd}（运行 $(basename "$0") help）" ;;
  esac
}

main "$@"
