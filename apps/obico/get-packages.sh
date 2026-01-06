#!/bin/sh
# Download Python packages for Obico
# Runs in Docker during SWU build

set -e
OBICO_DIRECTORY="${OBICO_DIRECTORY:-$(dirname $(realpath $0))}"
LIB_DIR="$OBICO_DIRECTORY/lib/python3.11/site-packages"
REQUIREMENTS="$OBICO_DIRECTORY/moonraker-obico/requirements.txt"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $*"; }

[ ! -f "$REQUIREMENTS" ] && { log "ERROR: requirements.txt not found"; exit 1; }

log "Installing packages from requirements.txt to $LIB_DIR"
mkdir -p "$LIB_DIR"

pip3 install --target="$LIB_DIR" -r "$REQUIREMENTS" 2>&1 | tail -30

log "Done: $LIB_DIR"
