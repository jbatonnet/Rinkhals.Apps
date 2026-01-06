#!/bin/sh

[ -f /useremain/rinkhals/.current/tools.sh ] && . /useremain/rinkhals/.current/tools.sh
RINKHALS_HOME="${RINKHALS_HOME:-/useremain/home/rinkhals}"

APP_ROOT=$(dirname $(realpath $0))
OBICO_DIR="$APP_ROOT/moonraker-obico"
OBICO_CFG="$RINKHALS_HOME/printer_data/config/moonraker-obico.cfg"
OBICO_LOG="$RINKHALS_HOME/printer_data/logs/moonraker-obico.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $*"; }

wait_for_moonraker() {
    log "Waiting for Moonraker..."
    command -v curl >/dev/null 2>&1 || return 1
    for i in $(seq 1 30); do
        curl -sf http://127.0.0.1:7125/printer/info >/dev/null 2>&1 && { log "Moonraker ready"; return 0; }
        sleep 2
    done
    log "Moonraker timeout, continuing"
    return 1
}

setup_venv() {
    cd "$APP_ROOT"
    [ ! -d "$APP_ROOT/bin" ] && { log "Creating venv..."; python -m venv --without-pip . || return 1; }
    [ ! -f "$APP_ROOT/bin/activate" ] && return 1
    . bin/activate
    export PYTHONPATH="$APP_ROOT/lib/python3.11/site-packages:$OBICO_DIR:$PYTHONPATH"
    return 0
}

# Main
log "=========================================="
log "Obico starting"

[ ! -d "$OBICO_DIR" ] && { log "ERROR: $OBICO_DIR not found"; exit 1; }
[ ! -f "$OBICO_CFG" ] && { log "Running install.sh"; "$APP_ROOT/install.sh" || exit 1; }

wait_for_moonraker
setup_venv || exit 1

# Run initializer (checks relink property, link status, starts linking if needed)
log "Running initializer..."
python "$APP_ROOT/obico_initializer.py"

# Start moonraker_obico (the main Obico process)
log "Starting moonraker_obico..."
exec python -m moonraker_obico.app -c "$OBICO_CFG" >> "$OBICO_LOG" 2>&1
