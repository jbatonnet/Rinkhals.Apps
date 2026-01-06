#!/bin/sh
# Download Obico (moonraker-obico) from GitHub
# Runs only during SWU build in Docker (requires git)

set -e
OBICO_DIRECTORY="${OBICO_DIRECTORY:-$(dirname $(realpath $0))}"
OBICO_REPO="https://github.com/TheSpaghettiDetective/moonraker-obico"
OBICO_PATH="$OBICO_DIRECTORY/moonraker-obico"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $*"; }

log "Downloading Obico to $OBICO_DIRECTORY"

if [ -d "$OBICO_PATH/.git" ]; then
    log "Updating existing repository..."
    cd "$OBICO_PATH" && git pull 2>/dev/null || log "WARNING: git pull failed"
elif [ -d "$OBICO_PATH" ]; then
    log "Directory exists but not a git repo, skipping"
else
    log "Cloning repository..."
    git clone --depth 1 "$OBICO_REPO" "$OBICO_PATH"
fi

# Get version
cd "$OBICO_DIRECTORY"
VERSION="latest"
[ -d "$OBICO_PATH/.git" ] && VERSION=$(cd "$OBICO_PATH" && git describe --tags --always 2>/dev/null || echo "latest")
log "Obico version: $VERSION"

# Update app.json
[ -f "$OBICO_DIRECTORY/app.json" ] && sed -i "s/\"version\": *\"[^\"]*\"/\"version\": \"$VERSION\"/" "$OBICO_DIRECTORY/app.json"

# Clean up unnecessary files (janus binaries don't work on uClibc - Kobra S1)
log "Cleaning up unnecessary files..."
rm -rf "$OBICO_PATH/.git"
rm -rf "$OBICO_PATH/docs"
rm -rf "$OBICO_PATH/scripts"
rm -rf "$OBICO_PATH/.github"
rm -rf "$OBICO_PATH/moonraker_obico/bin/janus"
rm -f "$OBICO_PATH/moonraker_obico/bin/ffmpeg/test-video.mp4"

# Patch for GoKlipper compatibility (macro variables return as strings, not ints)
log "Applying GoKlipper patches..."
PRINTER_PY="$OBICO_PATH/moonraker_obico/printer.py"
if [ -f "$PRINTER_PY" ]; then
    # Fix: convert macro_current_layer to int before comparison
    sed -i 's/if macro_current_layer is not None and macro_current_layer > 0:/if macro_current_layer is not None and int(macro_current_layer) > 0:/' "$PRINTER_PY"
    sed -i 's/current_layer = macro_current_layer$/current_layer = int(macro_current_layer)/' "$PRINTER_PY"
    log "Patched printer.py for GoKlipper"
fi

# Patch to skip ensure_include_cfgs.sh (we handle macros via install.sh)
MOONRAKER_CONN_PY="$OBICO_PATH/moonraker_obico/moonraker_conn.py"
if [ -f "$MOONRAKER_CONN_PY" ]; then
    # Add check for script existence before running it
    sed -i 's|ensure_include_cfgs_sh = os.path.join|ensure_include_cfgs_sh = os.path.join|' "$MOONRAKER_CONN_PY"
    sed -i '/cmd = f.*ensure_include_cfgs_sh/i\        if not os.path.exists(ensure_include_cfgs_sh): return' "$MOONRAKER_CONN_PY"
    log "Patched moonraker_conn.py to skip missing scripts"
fi

log "Done: $OBICO_PATH"
