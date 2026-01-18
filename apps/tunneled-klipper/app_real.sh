#!/bin/sh
source /useremain/rinkhals/.current/tools.sh

export APP_ROOT="$(dirname "$(realpath "$0")")"

# ---------------- Model-aware config ----------------
# Map KOBRA_MODEL_CODE -> primary/secondary serials
model_setup() {
    case "${KOBRA_MODEL_CODE:-}" in
        KS1)
            SERIAL_MCU="/dev/ttyS3"
            SERIAL_NOZZLE_MCU="/dev/ttyS5"
            ;;
        K3)
            SERIAL_MCU="/dev/ttyS3"
            SERIAL_NOZZLE_MCU="/dev/ttyS0"
            ;;
        K2P)
            SERIAL_MCU="/dev/ttyS3"
            SERIAL_NOZZLE_MCU=""
            ;;
        K3V2)
            SERIAL_MCU="/dev/ttyS3"
            SERIAL_NOZZLE_MCU="/dev/ttyS0"
            ;;            
        ""|*)
            echo "Error: Unknown or empty KOBRA_MODEL_CODE='${KOBRA_MODEL_CODE:-}'."
            echo "Supported: KS1, K3, K3V2, K2P. Export KOBRA_MODEL_CODE and try again."
            return 1
            ;;
    esac
}

pid_basename() {
    # e.g. turns /dev/ttyS3 -> ttyS3
    basename "$1"
}

pidfile_for_socat() {
    echo "/tmp/socat-$(pid_basename "$1").pid"
}

pidfile_for_launcher() {
    echo "/tmp/socat-launcher-$(pid_basename "$1").pid"
}

# ---------------- Utilities ----------------
get_by_pidfile() {
    pidfile="$1"
    [ -r "$pidfile" ] || return 1
    pid="$(cat "$pidfile" 2>/dev/null)" || return 1
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    kill -0 "$pid" 2>/dev/null || return 1
    printf '%s\n' "$pid"
}

# Only KS1 currently supported for MCU reset (GPIO116)
reset_mcus() {
    case "${KOBRA_MODEL_CODE:-}" in
        KS1)
            echo 116 > /sys/class/gpio/export 2>/dev/null || true
            echo out > /sys/class/gpio/gpio116/direction
            echo 0 > /sys/class/gpio/gpio116/value
            sleep 1
            echo 1 > /sys/class/gpio/gpio116/value
            ;;
        K3)
            echo "Note: reset_mcus() not implemented for model K3; skipping." >&2
            ;;
        K3V2)
            echo "Note: reset_mcus() not implemented for model K3V2; skipping." >&2
            ;;            
        K2P)
            echo "Note: reset_mcus() not applicable for K2P; skipping." >&2
            ;;
        *)
            echo "Note: reset_mcus() skipped due to unknown KOBRA_MODEL_CODE." >&2
            ;;
    esac
}

status() {
    # Model-aware PIDs if we can resolve; otherwise fall back to scanning known patterns
    PIDS=""
    if model_setup 2>/dev/null; then
        for dev in "$SERIAL_MCU" "$SERIAL_NOZZLE_MCU"; do
            [ -n "$dev" ] || continue
            for f in "$(pidfile_for_socat "$dev")" "$(pidfile_for_launcher "$dev")"; do
                pid="$(get_by_pidfile "$f")" || continue
                PIDS="${PIDS}${PIDS:+ }$pid"
            done
        done
    else
        # Fallback: scan any ttyS* PID files so 'status' still works without a model set
        for f in \
            /tmp/socat-ttyS*.pid \
            /tmp/socat-launcher-ttyS*.pid
        do
            [ -e "$f" ] || continue
            pid="$(get_by_pidfile "$f")" || continue
            PIDS="${PIDS}${PIDS:+ }$pid"
        done
    fi

    echo "Current PIDs: $PIDS"
    if [ -z "$PIDS" ]; then
        report_status $APP_STATUS_STOPPED
    else
        report_status $APP_STATUS_STARTED "$PIDS"
    fi
}

start() {
    # Require a supported model (do nothing if unknown)
    if ! model_setup; then
        exit 1
    fi

    cd "$APP_ROOT" || exit 1

    # Stop previous socat/launcher instances for our selected ports by PID file
    for dev in "$SERIAL_MCU" "$SERIAL_NOZZLE_MCU"; do
        [ -n "$dev" ] || continue
        for pidfile in "$(pidfile_for_socat "$dev")" "$(pidfile_for_launcher "$dev")"; do
            if [ -r "$pidfile" ]; then
                pid="$(cat "$pidfile" 2>/dev/null || true)"
                if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
                    kill "$pid" 2>/dev/null || true
                fi
                rm -f "$pidfile"
            fi
        done
    done

    # Legacy cleanup: ACE launchers if present
    for f in /tmp/socat-ace*-launcher.pid; do
        [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true
        [ -f "$f" ] && rm -f "$f"
    done

    # Stop gklib
    kill_by_name gklib

    chmod +x socat.sh 2>/dev/null || true

    # Waits for RPI gadget, starts the socat tunnels (model-aware)
    ./socat.sh auto-if=1.0 "$SERIAL_MCU"
    if [ -n "$SERIAL_NOZZLE_MCU" ]; then
        ./socat.sh auto-if=1.2 "$SERIAL_NOZZLE_MCU"
    fi
    # ./socat_ace.sh auto-if=1.4 ace=0

    # Allow full MCU (re)configuration
    reset_mcus
}

stop() {
    # Best-effort: if model is known, target our ports; otherwise fall back to generic sweep
    if model_setup 2>/dev/null; then
        for dev in "$SERIAL_MCU" "$SERIAL_NOZZLE_MCU"; do
            [ -n "$dev" ] || continue
            for pidfile in "$(pidfile_for_socat "$dev")" "$(pidfile_for_launcher "$dev")"; do
                if [ -r "$pidfile" ]; then
                    kill "$(cat "$pidfile")" 2>/dev/null || true
                    rm -f "$pidfile"
                fi
            done
        done
    else
        for pidfile in \
            /tmp/socat-ttyS*.pid \
            /tmp/socat-launcher-ttyS*.pid
        do
            [ -r "$pidfile" ] || continue
            kill "$(cat "$pidfile")" 2>/dev/null || true
            rm -f "$pidfile"
        done
    fi

    for f in /tmp/socat-ace*-launcher.pid; do
        [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true
        [ -f "$f" ] && rm -f "$f"
    done

    reset_mcus

    cd /userdata/app/gk || exit 0

    LD_LIBRARY_PATH=/userdata/app/gk:$LD_LIBRARY_PATH \
        ./gklib -a /tmp/unix_uds1 /userdata/app/gk/printer_data/config/printer.generated.cfg \
        &> "$RINKHALS_ROOT/logs/gklib.log" &
}

case "${1:-}" in
    status) status ;;
    start)  start ;;
    stop)   stop ;;
    *)
        echo "Usage: $0 {status|start|stop}" >&2
        exit 1
        ;;
esac
