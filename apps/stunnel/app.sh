APP_ROOT="$(dirname $(realpath $0))"

STUNNEL_CONF="$APP_ROOT/stunnel.conf"
PID_FILE=
KEY=
CRT=

init() {
  source /useremain/rinkhals/.current/tools.sh

  PID_FILE="$(get_config_value "$STUNNEL_CONF" pid)"
  KEY="$(get_config_value "$STUNNEL_CONF" key)"
  CRT="$(get_config_value "$STUNNEL_CONF" cert)"
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
    report_status $APP_STATUS_STARTED "$(cat "$PID_FILE")"
    return
  fi

  report_status $APP_STATUS_STOPPED
}

start() {
  stop
  crt_key_exist || create_crt_key
  stunnel "$STUNNEL_CONF"
}

stop() {
  [[ -f "$PID_FILE" ]] || return

  local pid=$(cat "$PID_FILE")
  kill_by_id "$pid"

  rm "$PID_FILE"
}

get_config_value() {
  local conf="$1"
  local key="$2"

  awk -v key="$key" -F'=' '
  BEGIN{ORS=""};                      # do not print additional LF
  {
      gsub(/[ \t\r\n]/, "", $1);      # remove spaces/tabs/CR/LF from key
      if ($1 == key) {                # check if cleaned key matches
          gsub(/[ \t\r\n]/, "", $2);  # remove spaces/tabs/CR/LF from value
          print $2;
          exit;                       # stop once found
      }
  }' "$conf"
}

create_crt_key() {
  openssl req -x509 -nodes -days 3650 -newkey rsa:4096 -keyout "$KEY" -out "$CRT" -config "$APP_ROOT/rinkhals_ssl.conf"
  chmod u=rw,g=,o= "$KEY"
  chmod u=rw,g=r,o=r "$CRT"
}

crt_key_exist() {
  [[ -f "$CRT" ]] && [[ -f "$KEY" ]]
}

if [[ "$1" != "--source-only" ]]; then
  init "$@"
  main "$@"
fi
