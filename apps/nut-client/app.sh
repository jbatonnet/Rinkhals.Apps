source /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname $(realpath $0))

status() {
    PIDS=$(get_by_name nut-client.py)

    if [ "$PIDS" == "" ]; then
        report_status $APP_STATUS_STOPPED
    else
        report_status $APP_STATUS_STARTED "$PIDS"
    fi
}

start() {
    stop
    cd $APP_ROOT
    chmod +x ./nut-client.py
    ./nut-client.py >> $RINKHALS_ROOT/logs/app-nut-client.log 2>&1 &
}

stop() {
    PIDS=$(ps | grep "nut-client.py" | grep -v grep | awk '{print $1}')
    for PID in $(echo "$PIDS"); do
        timeout -t 5 kill -15 $PID || timeout -30 kill -9 $PID
    done
}

case "$1" in
    status)
        status
        ;;
    start)
        start
        ;;
    stop)
        stop
        ;;
    *)
        echo "Usage: $0 {status|start|stop}" >&2
        exit 1
        ;;
esac
