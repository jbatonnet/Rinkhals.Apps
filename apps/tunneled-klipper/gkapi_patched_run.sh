#!/bin/sh
set -eu

GK_DIR="/userdata/app/gk"
BIN="gkapi"
PATCHED="$GK_DIR/$BIN.patch"
TMP="$GK_DIR/$BIN.patch.tmp"

die() { echo "gkapi_patched_run: $*" >&2; exit 1; }

need_env() {
    [ -n "${KOBRA_MODEL_CODE:-}" ] || die "KOBRA_MODEL_CODE not set"
    [ -n "${KOBRA_VERSION:-}" ] || die "KOBRA_VERSION not set"
}

patcher_path() {
    echo "/opt/rinkhals/patches/${BIN}.${KOBRA_MODEL_CODE}_${KOBRA_VERSION}.sh"
}

overlay_off() {
    # Remove bind overlay if present (power-safe default)
    if grep -q " $GK_DIR/$BIN " /proc/mounts 2>/dev/null; then
        umount "$GK_DIR/$BIN" 2>/dev/null || umount -l "$GK_DIR/$BIN" 2>/dev/null || true
    fi
}

build_patch() {
    need_env
    patcher="$(patcher_path)"
    [ -f "$patcher" ] || die "missing patcher: $patcher"

    [ -f "$GK_DIR/$BIN" ] || die "missing $GK_DIR/$BIN"

    rm -f "$TMP" 2>/dev/null || true
    cp -a "$GK_DIR/$BIN" "$TMP"
    "$patcher" "$TMP" >/dev/null 2>&1 || die "patcher failed"
    chmod +x "$TMP" 2>/dev/null || true

    mv -f "$TMP" "$PATCHED"
    sync || true
}

overlay_on() {
    [ -f "$PATCHED" ] || die "missing patched artifact: $PATCHED"
    overlay_off
    mount --bind "$PATCHED" "$GK_DIR/$BIN" || die "bind-mount failed"
}

stop_gkapi() {
    killall -q "$BIN" 2>/dev/null || true
}

start_gkapi() {
    cd "$GK_DIR" || exit 1
    export USE_MUTABLE_CONFIG="${USE_MUTABLE_CONFIG:-1}"
    export LD_LIBRARY_PATH="$GK_DIR:${LD_LIBRARY_PATH:-}"
    mkdir -p "${RINKHALS_ROOT:-/tmp}/logs" 2>/dev/null || true
    nohup "./$BIN" >> "${RINKHALS_ROOT:-/tmp}/logs/gkapi.log" 2>&1 &
}

case "${1:-}" in
    ensure-original)
        stop_gkapi
        overlay_off
        echo "OK: gkapi path is original (no overlay)"
        ;;
    run-patched)
        stop_gkapi
        build_patch
        overlay_on
        start_gkapi
        echo "OK: running patched gkapi via bind overlay"
        ;;
    *)
        echo "Usage: $0 {ensure-original|run-patched}" >&2
        exit 2
        ;;
esac
