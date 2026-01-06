#!/bin/sh

APP_ROOT=$(dirname $(realpath $0))
OBICO_DIR="$APP_ROOT/moonraker-obico"
RINKHALS_HOME="${RINKHALS_HOME:-/useremain/home/rinkhals}"
OBICO_CFG="$RINKHALS_HOME/printer_data/config/moonraker-obico.cfg"

[ ! -d "$OBICO_DIR" ] && { echo "ERROR: Obico not found"; exit 1; }
[ ! -f "$OBICO_CFG" ] && { echo "ERROR: Config not found, run install.sh first"; exit 1; }

case "$1" in --relink|-r) sed -i '/auth_token/d' "$OBICO_CFG" ;; esac

cd "$APP_ROOT"
[ ! -d bin ] && python -m venv --without-pip .
. bin/activate
export PYTHONPATH="$APP_ROOT/lib/python3.11/site-packages:$OBICO_DIR:$PYTHONPATH"
python -m moonraker_obico.link -c "$OBICO_CFG"
