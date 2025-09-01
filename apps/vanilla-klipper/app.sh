source /useremain/rinkhals/.current/tools.sh

export APP_ROOT=$(dirname $(realpath $0))

status() {
    PIDS=$(get_by_name klippy)

    if [ "$PIDS" == "" ]; then
        report_status $APP_STATUS_STOPPED
    else
        report_status $APP_STATUS_STARTED "$PIDS"
    fi
}

reset_mcus() {
    if [ "$KOBRA_MODEL_CODE" = "KS1" ]; then
        echo "KS1: Reseting MCU(s)"
        echo 116 > /sys/class/gpio/export 2>/dev/null
        echo out > /sys/class/gpio/gpio116/direction 2>/dev/null
        echo 0 > /sys/class/gpio/gpio116/value 2>/dev/null
        sleep 1
        echo 1 > /sys/class/gpio/gpio116/value 2>/dev/null
    else
        echo "MCU reset not implemented"
    fi
}

start() {
    #Stop klippy in case it's running
    kill_by_name klippy
    # Stop gklib
    kill_by_name gklib

    reset_mcus

    # Start Klippy
    cd $APP_ROOT
    chmod +x klippy.sh
    ./klippy.sh &
}

debug() {
    kill_by_name klippy
    kill_by_name gklib
    reset_mcus

    cd $APP_ROOT
    # Create Python venv
    python -m venv --without-pip $APP_ROOT
    . bin/activate

    # Start OctoApp
    cd klippy
    python -m klippy -a /tmp/unix_uds1  /userdata/app/gk/printer_data/config/printer.klipper.cfg >> /tmp/klippy.log 2>&1 &
}

stop() {
    kill_by_name klippy
    
    #MCU reset to allow gklib MCU configuration
    reset_mcus

    cd /userdata/app/gk

    LD_LIBRARY_PATH=/userdata/app/gk:$LD_LIBRARY_PATH \
        ./gklib -a /tmp/unix_uds1 /userdata/app/gk/printer_data/config/printer.generated.cfg &> $RINKHALS_ROOT/logs/gklib.log &
}

case "$1" in
    status)
        status
        ;;
    start)
        start
        ;;
    debug)
        shift
        debug $@
        ;;
    stop)
        stop
        ;;
    *)
        echo "Usage: $0 {status|start|debug|stop}" >&2
        exit 1
        ;;
esac
