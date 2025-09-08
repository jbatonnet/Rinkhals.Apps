#!/bin/sh
# PWM jingle player

PWMCHIP=${PWMCHIP:-0}
PWMM=${PWMM:-0}
BASE="/sys/class/pwm/pwmchip${PWMCHIP}/pwm${PWMM}"

# --- locking (single-tenant) -----------------------------------------------
LOCKDIR=${LOCKDIR:-/tmp/pwmjingle.lock}
LOCK_TIMEOUT_MS=${LOCK_TIMEOUT_MS:-3000}   # total wait time
LOCK_SLEEP_MS=${LOCK_SLEEP_MS:-50}         # step sleep

_calc_max_iters() {
  lt="${LOCK_TIMEOUT_MS:-3000}"; ls="${LOCK_SLEEP_MS:-50}"
  case "$ls" in ''|*[!0-9]*|0) ls=50 ;; esac
  case "$lt" in ''|*[!0-9]*) lt=3000 ;; esac
  # ceil(lt/ls)
  echo $(( (lt + ls - 1) / ls ))
}

lock_acquire() {
  max="$(_calc_max_iters)"; [ "$max" -le 0 ] && max=1
  i=0
  while ! mkdir "$LOCKDIR" 2>/dev/null; do
    # stale lock breaker if PID dead
    if [ -e "$LOCKDIR/pid" ]; then
      oldpid="$(cat "$LOCKDIR/pid" 2>/dev/null)"
      if [ -n "$oldpid" ] && ! kill -0 "$oldpid" 2>/dev/null; then
        rm -rf "$LOCKDIR" 2>/dev/null
        continue
      fi
    fi
    i=$((i+1))
    [ "$i" -ge "$max" ] && return 1
    /bin/busybox usleep $(( ${LOCK_SLEEP_MS:-50} * 1000 )) 2>/dev/null || sleep 1
  done
  echo $$ > "$LOCKDIR/pid"
  return 0
}
lock_release() {
  [ -d "$LOCKDIR" ] || return 0
  if [ -f "$LOCKDIR/pid" ] && [ "$(cat "$LOCKDIR/pid" 2>/dev/null)" = "$$" ]; then
    rm -rf "$LOCKDIR" 2>/dev/null || true
  fi
}

# --- helpers ----------------------------------------------------------------
DEFAULT_DUTY=${DEFAULT_DUTY:-40}

uslp() { /bin/busybox usleep "${1:-0}" 2>/dev/null || sleep $(( ( ${1:-0} + 999999 ) / 1000000 )); }
mslp() { uslp $(( ${1:-0} * 1000 )); }

w() { echo -n "$2" > "$1"; }

exists_or_export() {
  DID_EXPORT=0
  if [ ! -d "$BASE" ] && [ -e "/sys/class/pwm/pwmchip${PWMCHIP}/export" ]; then
    echo "${PWMM}" > "/sys/class/pwm/pwmchip${PWMCHIP}/export" || true
    DID_EXPORT=1
    mslp 50
  fi
}

# Set current note with period quantization: note <freq Hz> <duty%>
note() {
  f="$1"; d="${2:-$DEFAULT_DUTY}"
  case "$f" in ''|*[!0-9]*) return 1 ;; esac
  [ "$f" -le 0 ] && return 1

  # raw period in ns (int via awk)
  p_raw=$(awk -v f="$f" 'BEGIN{ if (f<=0) exit 1; printf("%d",1000000000.0/f) }') || return 1

  try_write_period() {
    P="$1"
    DN=$(awk -v p="$P" -v d="$d" 'BEGIN{
      if (p<=0) exit 1;
      printf("%d", p*d/100.0)
    }') || return 1
    [ "$DN" -ge "$P" ] && DN=$((P/2))
    [ "$DN" -lt 1 ] && DN=1
    echo 0 > "$BASE/enable" 2>/dev/null || true
    echo 0 > "$BASE/duty_cycle" 2>/dev/null || true
    echo "$P"  > "$BASE/period"     2>/dev/null || return 1
    echo "$DN" > "$BASE/duty_cycle" 2>/dev/null || return 1
    return 0
  }

  # Round to nearest 1000ns, then 10000ns, else fallback
  p1=$(( (p_raw + 500) / 1000 * 1000 ))
  if try_write_period "$p1"; then return; fi
  p2=$(( (p_raw + 5000) / 10000 * 10000 ))
  if try_write_period "$p2"; then return; fi
  try_write_period 400000 || return
}

beep() {
  m="${1:-200}"
  if ! echo 1 > "$BASE/enable" 2>/dev/null; then
    P=$(cat "$BASE/period" 2>/dev/null || echo 400000)
    case "$P" in ''|*[!0-9]*) P=400000 ;; esac
    DN=$(( P/2 )); [ "$DN" -lt 1 ] && DN=1
    echo "$DN" > "$BASE/duty_cycle" 2>/dev/null || true
    echo 1 > "$BASE/enable" 2>/dev/null || return
  fi
  /bin/busybox usleep $((m*1000)) 2>/dev/null || sleep $(( (m+999)/1000 ))
  echo 0 > "$BASE/enable" 2>/dev/null || true
}

rest() { mslp "${1:-60}"; }

panic_off_cmd() { echo 0 > "$BASE/enable" 2>/dev/null; echo 0 > "$BASE/duty_cycle" 2>/dev/null; }
stop_now() { panic_off_cmd; }

# --- jingles ----------------------------------------------------------------
ta_da() { note 392; beep 180; rest 50; note 523; beep 280; }
sad()   { note 466; beep 220; rest 40; note 415; beep 220; rest 40; note 349; beep 450; }
coin()  { note 1046; beep 120; rest 40; note 1318; beep 150; }
error() { note 220; beep 200; rest 80; note 220; beep 200; }
ok_jingle() { note 392; beep 120; rest 40; note 523; beep 120; rest 40; note 659; beep 180; }
indy()  { note 330; beep 180; rest 60; note 349; beep 180; rest 60; note 392; beep 400; }
imperial() { note 392; beep 250; rest 50; note 392; beep 250; rest 50; note 392; beep 250; rest 200; note 311; beep 250; rest 50; note 466; beep 250; rest 50; note 392; beep 400; }
mario_coin()  { note 1318; beep 120; rest 40; note 1567; beep 120; }
mario_start() { note 523; beep 120; rest 40; note 659; beep 120; rest 40; note 784; beep 120; rest 40; note 1046; beep 160; rest 40; note 784; beep 200; }
failure() { note 262; beep 300; rest 100; note 220; beep 300; rest 100; note 175; beep 600; }
usb_detect() { note 784; beep 160; rest 80; note 988; beep 220; }
usb_remove() { note 988; beep 160; rest 80; note 784; beep 220; }

# --- main ------------------------------------------------------------------

# Acquire lock (single-tenant)
if ! lock_acquire; then
  echo "pwm_jingle: busy (another jingle playing). Try again." >&2
  exit 3
fi
cleanup_lock() { lock_release; }
trap cleanup_lock EXIT INT TERM

exists_or_export
[ ! -d "$BASE" ] && { echo "PWM pwmchip${PWMCHIP}/pwm${PWMM} not available"; exit 1; }

# Snapshot current state
ORIG_ENABLE="$(cat "$BASE/enable" 2>/dev/null || echo 0)"
ORIG_PERIOD="$(cat "$BASE/period" 2>/dev/null || echo)"
ORIG_DUTY="$(cat "$BASE/duty_cycle" 2>/dev/null || echo)"
ORIG_POLARITY="$(cat "$BASE/polarity" 2>/dev/null || echo)"

restore() {
  panic_off_cmd
  uslp 5000  # settle
  if [ -n "$ORIG_POLARITY" ] && [ -f "$BASE/polarity" ]; then
    echo "$ORIG_POLARITY" > "$BASE/polarity" 2>/dev/null || true
  fi
  if [ -n "$ORIG_PERIOD" ] && [ -n "$ORIG_DUTY" ]; then
    RESTORE_P="$ORIG_PERIOD"; RESTORE_D="$ORIG_DUTY"
    case "$RESTORE_P" in ''|*[!0-9]*) RESTORE_P=400000 ;; esac
    case "$RESTORE_D" in ''|*[!0-9]*) RESTORE_D=$((RESTORE_P/2)) ;; esac
    [ "$RESTORE_D" -ge "$RESTORE_P" ] && RESTORE_D=$((RESTORE_P/2))
    echo "$RESTORE_P" > "$BASE/period"     2>/dev/null || true
    echo "$RESTORE_D" > "$BASE/duty_cycle" 2>/dev/null || true
  fi
  echo "${ORIG_ENABLE:-0}" > "$BASE/enable" 2>/dev/null || true
  if [ "${UNEXPORT:-0}" -eq 1 ] && [ "${DID_EXPORT:-0}" -eq 1 ]; then
    echo 0 > "$BASE/enable" 2>/dev/null || true
    if [ -e "/sys/class/pwm/pwmchip${PWMCHIP}/unexport" ]; then
      echo "${PWMM}" > "/sys/class/pwm/pwmchip${PWMCHIP}/unexport" 2>/dev/null || true
    fi
  fi
}
trap restore EXIT INT TERM

case "$1" in
  ta-da|tada|ta_da) ta_da ;;
  sad|wah|trombone) sad ;;
  coin) coin ;;
  error|buzz) error ;;
  ok|success) ok_jingle ;;
  indy|indiana) indy ;;
  imperial|vader) imperial ;;
  mario|coin) mario_coin ;;
  mario_start|start) mario_start ;;
  failure|fail|death) failure ;;
  usb|plug|detect|usb-detect) usb_detect ;;
  usb_remove|unplug|usb-remove) usb_remove ;;
  tone)
    case "${2:-440}" in ''|*[!0-9]*) f=440 ;; *) f="${2}" ;; esac
    [ "$f" -le 0 ] && f=440
    note "$f" "${4:-$DEFAULT_DUTY}"; beep "${3:-250}"
    ;;
  panic_off) panic_off_cmd ;;
  stop|halt) stop_now ;;
  test) note 1000; beep 200; rest 80; note 800; beep 200 ;;
  *)
    echo "Usage: PWMCHIP=0 PWMM=0 $0 {ta-da|sad|coin|error|ok|indy|imperial|mario|mario_start|failure|usb|usb-detect|usb_remove|usb-remove|tone <freq Hz> <ms> <duty%>|test|panic_off|stop}" >&2
    exit 2
    ;;
esac

