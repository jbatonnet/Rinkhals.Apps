#!/bin/sh
# socat_ace.sh
# Usage examples:
#   ./socat_ace.sh auto-if=1.4 ace=0
#   ./socat_ace.sh /dev/ttyS3 ace=1
#   ./socat_ace.sh /dev/ttyACM2 ace=2
#
# Notes:
# - Destination must be ACE (ace=N). Source can be a concrete serial device or auto-if=X.Y.
# - Launcher PID file is /tmp/socat-ace<N>-launcher.pid (N from ace=).
# - RPi USB-Gadget VID/PID defaults: VID=1d6b PID=0104 (override via env).

set -eu

if [ $# -ne 2 ]; then
  echo "Usage: $0 <src-serial|auto-if=X.Y|/dev/ttyACM*> ace=N" >&2
  exit 1
fi

SRC="$1"
DST="$2"

case "$DST" in
  ace=*) ACE_IDX="${DST#ace=}" ;;
  *)
    echo "Destination must be 'ace=N' (got: $DST)" >&2
    exit 1
    ;;
esac

sanitize_key() { echo "$1" | tr '/[:space:]' '_' ; }
DST_KEY="$(sanitize_key "$DST")"
PAIR_KEY="$(sanitize_key "${SRC}_${DST}")"

PID_FILE="/tmp/socat-${DST_KEY}.pid"
LAUNCHER_PID_FILE="/tmp/socat-ace${ACE_IDX}-launcher.pid"
LOCKFILE="/tmp/socat-${PAIR_KEY}.lock"
LOG_FILE="/tmp/socat-${DST_KEY}.log"

# Defaults for RPi USB-Gadget (used by auto-if=)
VID="${VID:-1d6b}"
PID_USB="${PID:-0104}"

export SRC DST ACE_IDX PID_FILE LAUNCHER_PID_FILE LOCKFILE LOG_FILE VID PID_USB

mkdir -p /tmp 2>/dev/null || true

# -------- Acquire lock (symlink with PID tracking) --------
ACQUIRE_TIMEOUT_SEC=5
SLEEP_STEP=0.1
ELAPSED=0

try_lock() { ln -s "$$" "$LOCKFILE" 2>/dev/null; }

while ! try_lock; do
  if [ -L "$LOCKFILE" ]; then
    owner_pid="$(readlink "$LOCKFILE" 2>/dev/null || echo "")"
    if [ -n "$owner_pid" ] && ! kill -0 "$owner_pid" 2>/dev/null; then
      # stale lock → remove and retry
      rm -f "$LOCKFILE" 2>/dev/null || true
      continue
    fi
  fi
  sleep "$SLEEP_STEP"
  ELAPSED=$(awk "BEGIN{print $ELAPSED + $SLEEP_STEP}")
  if awk "BEGIN{exit !($ELAPSED >= $ACQUIRE_TIMEOUT_SEC)}"; then
    echo "Could not acquire lock for $SRC -> $DST (held by PID ${owner_pid:-unknown})" >&2
    exit 1
  fi
done

parent_lock_cleanup() { rm -f "$LOCKFILE" 2>/dev/null || true; }
trap parent_lock_cleanup EXIT

# -------- Forked Launcher --------
setsid sh -c '
  set -eu
  exec </dev/null >>"$LOG_FILE" 2>&1

  SOCAT_PID=""

  cleanup() {
    # kill socat group if running
    _pid=""
    if [ -n "${SOCAT_PID:-}" ]; then
      _pid="$SOCAT_PID"
    elif [ -f "$PID_FILE" ]; then
      _pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    fi
    if [ -n "$_pid" ] && kill -0 "$_pid" 2>/dev/null; then
      kill -TERM "-$_pid" 2>/dev/null || kill -TERM "$_pid" 2>/dev/null || true
      sleep 0.2
      kill -KILL "-$_pid" 2>/dev/null || kill -KILL "$_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$LAUNCHER_PID_FILE" "$LOCKFILE" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  rl() { command -v readlink >/dev/null 2>&1 && readlink -f "$1" 2>/dev/null || echo "$1"; }

  get_usb_parent() {
    p="$(rl "$1")"
    while [ "$p" != "/" ] && [ ! -f "$p/idVendor" ]; do p="$(dirname "$p")"; done
    [ -f "$p/idVendor" ] && echo "$p" || echo ""
  }

  find_acm_by_ifnum() {
    want_if="$1"
    for t in /sys/class/tty/ttyACM*; do
      [ -e "$t" ] || continue
      iface_dir="$(rl "$t/device" 2>/dev/null)" || continue
      usb_parent="$(get_usb_parent "$iface_dir")" || continue
      vid="$(tr "A-Z" "a-z" < "$usb_parent/idVendor" 2>/dev/null || true)"
      pid="$(tr "A-Z" "a-z" < "$usb_parent/idProduct" 2>/dev/null || true)"
      [ "$vid" = "$VID" ] && [ "$pid" = "$PID_USB" ] || continue
      base="$(basename "$iface_dir")"; ifnum="${base#*:}"
      [ "$ifnum" = "$want_if" ] && { echo "/dev/$(basename "$t")"; return 0; }
    done
    return 1
  }

  find_ace_by_idx() {
    idx="$1"; byid="/dev/serial/by-id"
    [ -d "$byid" ] || return 1
    for p in "$byid"/usb-ANYCUBIC_ACE_"$idx"-* "$byid"/ANYCUBIC_ACE_"$idx"-*; do
      [ -e "$p" ] || continue
      dev="$(rl "$p")"; [ -n "$dev" ] && [ -c "$dev" ] && { echo "$dev"; return 0; }
    done
    return 1
  }

  resolve_src() {
    s="$1"
    case "$s" in
      auto-if=*)
        ifnum="${s#auto-if=}"
        echo "[$(date +%F_%T)] Waiting for RPi gadget ACM (VID:$VID PID:$PID_USB IF:$ifnum)..." >&2
        while :; do
          dev="$(find_acm_by_ifnum "$ifnum" || true)"
          [ -n "$dev" ] && [ -c "$dev" ] && { echo "$dev"; return 0; }
          sleep 1
        done
        ;;
      *)
        echo "[$(date +%F_%T)] Waiting for source device $s ..." >&2
        while [ ! -c "$s" ]; do sleep 1; done
        echo "$s"
        ;;
    esac
  }

  resolve_dst() {
    echo "[$(date +%F_%T)] Waiting for ANYCUBIC ACE idx=$ACE_IDX via /dev/serial/by-id ..." >&2
    while :; do
      dev="$(find_ace_by_idx "$ACE_IDX" || true)"
      [ -n "$dev" ] && [ -c "$dev" ] && { echo "$dev"; return 0; }
      sleep 1
    done
  }

  echo "[$(date +%F_%T)] Launcher started for SRC=$SRC, DST=$DST (ACE idx=$ACE_IDX)" >&2

  while :; do
    SRC_DEV="$(resolve_src "$SRC")"
    DST_DEV="$(resolve_dst)"

    # Kill any old socat before restart
    if [ -f "$PID_FILE" ]; then
      OLD="$(cat "$PID_FILE" 2>/dev/null || true)"
      if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
        echo "[$(date +%F_%T)] Found running socat ($OLD); terminating before restart..." >&2
        kill -TERM "-$OLD" 2>/dev/null || kill -TERM "$OLD" 2>/dev/null || true
        sleep 0.2
        kill -KILL "-$OLD" 2>/dev/null || kill -KILL "$OLD" 2>/dev/null || true
      fi
      rm -f "$PID_FILE"
    fi

    echo "[$(date +%F_%T)] Using SRC=$SRC_DEV, DST=$DST_DEV; starting socat." >&2

    # Start socat in its own process group (setsid) and record PID
    setsid socat -d -d -x -v \
      "OPEN:${SRC_DEV},b115200,nonblock,raw,echo=0,clocal=1,cread=1,hupcl=0,crtscts=0,ixon=0,ixoff=0,ixany=0,opost=0,onlcr=0,icrnl=0,inlcr=0                            ,igncr=0,parenb=0,cs8" \
      "OPEN:${DST_DEV},b115200,nonblock,raw,echo=0,clocal=1,cread=1,hupcl=0,crtscts=0,ixon=0,ixoff=0,ixany=0,opost=0,onlcr=0,icrnl=0,inlcr=0                            ,igncr=0,parenb=0,cs8" &

    SOCAT_PID=$!
    echo "$SOCAT_PID" > "$PID_FILE"
    echo "[$(date +%F_%T)] socat PID $SOCAT_PID written to $PID_FILE" >&2

    wait "$SOCAT_PID" || true
    echo "[$(date +%F_%T)] socat exited, retrying in 1s..." >&2
    sleep 1
  done
' </dev/null >>"$LOG_FILE" 2>&1 &

LAUNCHER_PID=$!
echo "$LAUNCHER_PID" > "$LAUNCHER_PID_FILE"
echo "Started bridge launcher for: ${SRC} -> ${DST} (PID $LAUNCHER_PID; launcher PID file: $LAUNCHER_PID_FILE)"

