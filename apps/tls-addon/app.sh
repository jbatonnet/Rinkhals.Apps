APP_ROOT="$(dirname $(realpath $0))"

CRT="${CRT:-/useremain/home/rinkhals/printer_data/certs/moonraker.cert}"
KEY="${KEY:-/useremain/home/rinkhals/printer_data/certs/moonraker.key}"

PID_FILE="/tmp/rinkhals/tls-addon.pids"

init() {
  source /useremain/rinkhals/.current/tools.sh
}

main() {
  local command="$1"

  case "$command" in
    help)
      help
      exit 0
      ;;
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
      help
      exit 1
      ;;
  esac
}

help() {
  echo "Usage: $0 {status|start|stop}" >&2
}

status() {
  if [[ -f "$PID_FILE" ]]; then
    report_status $APP_STATUS_STARTED "$(cat "$PID_FILE" | xargs)"
    return
  fi

  report_status $APP_STATUS_STOPPED
}

start() {
  stop

  crt_key_exist || create_crt_key

  while read -r service listen target; do
    [[ -z "$service" ]] && continue             # skip empty lines
    case "$service" in \#*) continue ;; esac    # skip commented lines

    echo "Mapping service $service: $listen(https) -> $target(http)" >&2
    local pid

    pid="$(add_mapping $listen $target)"
    [[ "$?" == 0 ]] && echo "$pid" >> "$PID_FILE"
  done < "$APP_ROOT/port_mappings.conf"
}

stop() {
  [[ -f "$PID_FILE" ]] || return

  while read pid; do
    kill_by_id "$pid"
  done < "$PID_FILE"

  rm "$PID_FILE"
}

add_mapping() {
  local listen target pid

  listen="$1"
  target="$2"

  echo socat "OPENSSL-LISTEN:$listen,cert=$CRT,key=$KEY,verify=0,reuseaddr,fork TCP:127.0.0.1:$target" 1> /dev/null &
  pid=$!

  if [[ "$?" == 0 ]]; then
    echo $pid
    return 0
  else
    echo 0
    return 1
  fi
}

create_crt_key() {
  openssl req -x509 -nodes -days 3650 -newkey rsa:4096 -keyout "$KEY" -out "$CRT" -config "$APP_ROOT/rinkhals_ssl.conf"
}

crt_key_exist() {
  [[ -f "$CRT" ]] && [[ -f "$KEY" ]]
}

if [[ "$1" != "--source-only" ]]; then
  init "$@"
  main "$@"
fi
