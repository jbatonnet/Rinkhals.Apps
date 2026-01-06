#!/bin/sh

. /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname $(realpath $0))
OBICO_DIR="$APP_ROOT/moonraker-obico"
OBICO_CFG="$RINKHALS_HOME/printer_data/config/moonraker-obico.cfg"
APP_LOG="$RINKHALS_HOME/printer_data/logs/obico-app.log"

mkdir -p "$RINKHALS_HOME/printer_data/config" "$RINKHALS_HOME/printer_data/logs"

version() { grep '"version"' "$APP_ROOT/app.json" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || echo "unknown"; }

status() {
    PIDS=$(get_by_name obico-start.sh)
    [ -z "$PIDS" ] && PIDS=$(get_by_name moonraker_obico)
    [ -z "$PIDS" ] && report_status $APP_STATUS_STOPPED || report_status $APP_STATUS_STARTED "$PIDS"
}

start() {
    log "Obico: starting..."
    stop
    [ ! -f "$APP_ROOT/obico-start.sh" ] && { log "Obico: ERROR obico-start.sh not found"; return 1; }
    chmod +x "$APP_ROOT/obico-start.sh"
    nohup "$APP_ROOT/obico-start.sh" >> "$APP_LOG" 2>&1 &
}

stop() {
    kill_by_name obico-start.sh 2>/dev/null || true
    kill_by_name moonraker_obico 2>/dev/null || true
    pkill -f "moonraker_obico.app" 2>/dev/null || true
    pkill -f "obico_initializer.py" 2>/dev/null || true
}

debug() {
    [ ! -d "$OBICO_DIR" ] && { echo "ERROR: Obico not found at $OBICO_DIR"; return 1; }
    [ ! -f "$OBICO_CFG" ] && "$APP_ROOT/install.sh"
    stop
    cd "$APP_ROOT"
    [ ! -d "$APP_ROOT/bin" ] && python -m venv --without-pip .
    . bin/activate
    export PYTHONPATH="$APP_ROOT/lib/python3.11/site-packages:$OBICO_DIR:$PYTHONPATH"
    python -m moonraker_obico.app -c "$OBICO_CFG" "$@"
}

case "$1" in
    version) version ;;
    status)  status ;;
    start)   start ;;
    stop)    stop ;;
    debug)   shift; debug "$@" ;;
    *)       echo "Usage: $0 {version|status|start|stop|debug}" >&2; exit 1 ;;
esac
