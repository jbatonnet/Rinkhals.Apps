#!/bin/sh

APP_ROOT=$(dirname $(realpath $0))
OBICO_DIR="$APP_ROOT/moonraker-obico"
RINKHALS_HOME="${RINKHALS_HOME:-/useremain/home/rinkhals}"
CONFIG_DIR="$RINKHALS_HOME/printer_data/config"
LOG_DIR="$RINKHALS_HOME/printer_data/logs"
OBICO_CFG="$CONFIG_DIR/moonraker-obico.cfg"

[ -f /useremain/rinkhals/.current/tools.sh ] && . /useremain/rinkhals/.current/tools.sh
log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $*"; }

mkdir -p "$CONFIG_DIR" "$LOG_DIR"
[ ! -d "$OBICO_DIR" ] && { log "ERROR: Obico not found at $OBICO_DIR"; exit 1; }

if [ ! -f "$OBICO_CFG" ]; then
    log "Creating config at $OBICO_CFG"
    cat > "$OBICO_CFG" <<EOF
[server]
url = https://app.obico.io

[moonraker]
host = 127.0.0.1
port = 7125

[webcam]
disable_video_streaming = False

[logging]
path = ${LOG_DIR}/moonraker-obico.log
EOF
fi

PRINTER_CFG="$CONFIG_DIR/printer.custom.cfg"
MACRO_SRC="$APP_ROOT/include_cfgs/moonraker_obico_macros.cfg"
if [ -f "$MACRO_SRC" ] && ! grep -q "_OBICO_LAYER_CHANGE" "$PRINTER_CFG" 2>/dev/null; then
    log "Adding Obico macros to printer.custom.cfg"
    [ ! -f "$PRINTER_CFG" ] && touch "$PRINTER_CFG"
    echo "" >> "$PRINTER_CFG"
    cat "$MACRO_SRC" >> "$PRINTER_CFG"
fi

log "Installation complete"
