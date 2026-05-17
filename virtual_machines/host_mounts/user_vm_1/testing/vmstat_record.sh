#!/usr/bin/env bash
set -euo pipefail

DIR="${VMSTAT_DIR:-./vmstat_logs}"
PID_FILE="$DIR/vmstat.pid"
LOG_FILE_PATH="$DIR/current_log_path"

mkdir -p "$DIR"

case "${1:-}" in
  start)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "vmstat уже запущен"
      echo "Лог: $(cat "$LOG_FILE_PATH")"
      exit 1
    fi

    LOG="$DIR/vmstat_$(date +%Y%m%d_%H%M%S).log"

    {
      echo "# started_at=$(date -Is)"
      echo "# host=$(hostname)"
      echo "# cmd=vmstat -t -n 1"
    } > "$LOG"

    nohup stdbuf -oL vmstat -t -n 1 >> "$LOG" 2>&1 &

    echo $! > "$PID_FILE"
    echo "$LOG" > "$LOG_FILE_PATH"

    echo "Запись началась"
    echo "PID: $(cat "$PID_FILE")"
    echo "Лог: $LOG"
    ;;

  stop)
    if [[ ! -f "$PID_FILE" ]]; then
      echo "vmstat не запущен"
      exit 1
    fi

    PID="$(cat "$PID_FILE")"
    LOG="$(cat "$LOG_FILE_PATH")"

    if kill -0 "$PID" 2>/dev/null; then
      kill "$PID"
      sleep 0.2
    fi

    echo "# stopped_at=$(date -Is)" >> "$LOG"

    rm -f "$PID_FILE"

    echo "Запись остановлена"
    echo "Лог: $LOG"
    ;;

  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "vmstat запущен"
      echo "PID: $(cat "$PID_FILE")"
      echo "Лог: $(cat "$LOG_FILE_PATH")"
    else
      echo "vmstat не запущен"
    fi
    ;;

  *)
    echo "Usage:"
    echo "  $0 start"
    echo "  $0 stop"
    echo "  $0 status"
    exit 1
    ;;
esac