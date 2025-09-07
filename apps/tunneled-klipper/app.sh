# app.sh launchwrapper
# A thin wrapper that defers start() by 10s when invoked by a parent "./start.sh"
# and returns immediately, while still exposing start/stop/status/debug entrypoints.

source /useremain/rinkhals/.current/tools.sh
export APP_ROOT=$(dirname $(realpath $0))

set -euo pipefail

ORIG_SCRIPT="${ORIG_SCRIPT:-"$APP_ROOT/app_real.sh"}"

STATE_DIR="/run/rinkhals"
WAIT_PID_FILE="$STATE_DIR/launchwrapper.wait.pid"
WAIT_UNTIL_FILE="$STATE_DIR/launchwrapper.wait.until"
mkdir -p "$STATE_DIR" 2>/dev/null || true

parent_cmdline() {
  tr '\0' ' ' < "/proc/$PPID/cmdline" | sed 's/[[:space:]]*$//'
}

start() {
  local parent
  parent="$(parent_cmdline)"

  if [[ "$parent" == *"./start.sh"* ]]; then
    # If there is already a waiting launcher, just report and return
    if [[ -f "$WAIT_PID_FILE" ]]; then
      local wpid
      wpid="$(cat "$WAIT_PID_FILE" 2>/dev/null || true)"
      if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
        echo "start(): already scheduled; launcher PID $wpid"
        return 0
      else
        rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE"
      fi
    fi

    local delay=15
    local until_ts=$(( $(date +%s) + delay ))
    echo "$until_ts" > "$WAIT_UNTIL_FILE"

    setsid nohup bash -c "
      sleep $delay
       ./pwm_jingle.sh indy
      \"$ORIG_SCRIPT\" start || true
      rm -f \"$WAIT_PID_FILE\" \"$WAIT_UNTIL_FILE\" 2>/dev/null || true
    " >/dev/null 2>&1 &

    echo $! > "$WAIT_PID_FILE"
    echo "Delayed start scheduled in ${delay}s (launcher PID $(cat "$WAIT_PID_FILE"))."
    ./pwm_jingle.sh ta-da
    # Return immediately while the detached launcher sleeps.
    return 0
  fi

  # Otherwise, call through immediately
  "$ORIG_SCRIPT" start
}

status() {
  if [[ -f "$WAIT_PID_FILE" ]]; then
    local wpid
    wpid="$(cat "$WAIT_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
      local rem=""
      if [[ -f "$WAIT_UNTIL_FILE" ]]; then
        local until_ts now_ts
        until_ts="$(cat "$WAIT_UNTIL_FILE" 2>/dev/null || echo 0)"
        now_ts="$(date +%s)"
        local delta=$(( until_ts - now_ts ))
        (( delta < 0 )) && delta=0
        rem=" (~${delta}s remaining)"
      fi
      echo "launchwrapper: delayed start pending; launcher PID ${wpid}${rem}"
    else
      rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE" 2>/dev/null || true
    fi
  fi

  "$ORIG_SCRIPT" status
}

stop() {
  if [[ -f "$WAIT_PID_FILE" ]]; then
    local wpid
    wpid="$(cat "$WAIT_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
      kill "$wpid" 2>/dev/null || true
    fi
    rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE" 2>/dev/null || true
  fi

  "$ORIG_SCRIPT" stop
}

debug() {
  "$ORIG_SCRIPT" debug
}

case "${1:-}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  debug)  shift; debug "$@" ;;
  *)
    echo "Usage: $0 {start|stop|status|debug}" >&2
    exit 1
    ;;
esac
