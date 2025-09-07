#!/bin/sh
# socat.sh
# Usage:
#   ./socat.sh auto-if=1.0 /dev/ttyS3
#   ./socat.sh auto-if=1.2 /dev/ttyS5
#   (or pass explicit /dev/ttyACM* as first arg to skip auto-detect)

set -eu

if [ $# -ne 2 ]; then
  echo "Usage: $0 <src-serial|auto-if=X.Y> <dst-serial>" >&2
  exit 1
fi

SRC="$1"
DST="$2"
DST_BASE="$(basename "$DST")"

PID_FILE="/tmp/socat-${DST_BASE}.pid"
LAUNCHER_PID_FILE="/tmp/socat-launcher-${DST_BASE}.pid"
LOG_FILE="/tmp/socat-${DST_BASE}.log"

# Defaults for the RPi USB gadget (override via env if needed)
VID="${VID:-1d6b}"
PID="${PID:-0104}"

export SRC DST PID_FILE LAUNCHER_PID_FILE LOG_FILE VID PID

mkdir -p /tmp 2>/dev/null || true

# Spawn a detached child that waits and then starts socat
setsid sh -c '
  set -eu
  exec </dev/null >>"$LOG_FILE" 2>&1

  cleanup() { rm -f "$LAUNCHER_PID_FILE" 2>/dev/null || true; }
  trap cleanup EXIT

  # BusyBox-friendly realpath
  rl() {
    if command -v readlink >/dev/null 2>&1; then
      readlink -f "$1" 2>/dev/null || echo "$1"
    else
      # crude fallback
      echo "$1"
    fi
  }

  # climb to USB parent that has idVendor/idProduct
  get_usb_parent() {
    p="$(rl "$1")"
    while [ "$p" != "/" ] && [ ! -f "$p/idVendor" ]; do
      p="$(dirname "$p")"
    done
    if [ -f "$p/idVendor" ]; then
      echo "$p"
    else
      echo ""
    fi
  }

  find_acm_by_ifnum() {
    want_if="$1"   # e.g. 1.0 or 1.2
    for t in /sys/class/tty/ttyACM*; do
      [ -e "$t" ] || continue
      iface_dir="$(rl "$t/device" 2>/dev/null)"
      [ -n "$iface_dir" ] || continue

      usb_parent="$(get_usb_parent "$iface_dir")"
      [ -n "$usb_parent" ] || continue

      vid="$(tr "A-Z" "a-z" < "$usb_parent/idVendor" 2>/dev/null || true)"
      pid="$(tr "A-Z" "a-z" < "$usb_parent/idProduct" 2>/dev/null || true)"
      [ "$vid" = "$VID" ] && [ "$pid" = "$PID" ] || continue

      base="$(basename "$iface_dir")"         # like 1-1.4:1.0
      ifnum="${base#*:}"                      # 1.0
      if [ "$ifnum" = "$want_if" ]; then
        echo "/dev/$(basename "$t")"
        return 0
      fi
    done
    return 1
  }

  resolve_src() {
    s="$1"
    case "$s" in
      auto-if=*)
        ifnum="${s#auto-if=}"
        # Wait loop until the matching ACM appears
        echo "[$(date +%F_%T)] Waiting for RPi gadget ACM (VID:$VID PID:$PID IF:$ifnum)..." >&2
        while : ; do
          dev="$(find_acm_by_ifnum "$ifnum" || true)"
          if [ -n "$dev" ] && [ -c "$dev" ]; then
            echo "$dev"
            return 0
          fi
          sleep 1
        done
        ;;
      *)
        # explicit path; wait until char device exists
        echo "[$(date +%F_%T)] Waiting for device $s ..." >&2
        while [ ! -c "$s" ]; do
          sleep 1
        done
        echo "$s"
        ;;
    esac
  }

  echo "[$(date +%F_%T)] Launcher started for DST=$DST" >&2

  SRC_DEV="$(resolve_src "$SRC")"
  echo "[$(date +%F_%T)] Using SRC=$SRC_DEV, DST=$DST; starting socat." >&2

  # Start socat and record PID
  nice -n -20 socat -d -d \
    "OPEN:${SRC_DEV},raw,echo=0" \
    "OPEN:${DST},raw,echo=0" &
  ./pwm_jingle.sh usb &
  echo $! > "$PID_FILE"
  echo "[$(date +%F_%T)] socat PID $(cat "$PID_FILE") written to $PID_FILE" >&2
' </dev/null >>"$LOG_FILE" 2>&1 &

LAUNCHER_PID=$!
echo "$LAUNCHER_PID" > "$LAUNCHER_PID_FILE"

echo "Started bridge launcher for: ${SRC} -> ${DST}"
