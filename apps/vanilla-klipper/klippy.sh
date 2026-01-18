. /useremain/rinkhals/.current/tools.sh

# Activate Python venv
python -m venv --without-pip $APP_ROOT
. bin/activate

# Prepare configuration
CONFIG_SOURCE=printer.klipper_${KOBRA_MODEL_CODE}.cfg
ACE_CONFIG_SOURCE=ace_${KOBRA_MODEL_CODE}.cfg
if [ ! -f $CONFIG_SOURCE ]; then
    exit 1
fi

CONFIG_DESTINATION=/userdata/app/gk/printer_data/config/printer.klipper.cfg
if [ ! -f $CONFIG_DESTINATION ]; then
    cp $CONFIG_SOURCE $CONFIG_DESTINATION
fi

#Copy printer specific ACE config if there is one
if [ -f $ACE_CONFIG_SOURCE ]; then
    ACE_CONFIG_DESTINATION=/userdata/app/gk/printer_data/config/$ACE_CONFIG_SOURCE
    GENERIC_MACROS_DESTINATION=/userdata/app/gk/printer_data/config/printer_generic_macros.cfg
    ACE_MACROS_GENERIC_DESTINATION=/userdata/app/gk/printer_data/config/ace_macros_generic.cfg

    if [ ! -f "$ACE_CONFIG_DESTINATION" ]; then
        cp "$ACE_CONFIG_SOURCE" "$ACE_CONFIG_DESTINATION"
    fi

    if [ ! -f "$GENERIC_MACROS_DESTINATION" ]; then
        cp printer_generic_macros.cfg "$GENERIC_MACROS_DESTINATION"
    fi

    if [ ! -f "$ACE_MACROS_GENERIC_DESTINATION" ]; then
        cp ace_macros_generic.cfg "$ACE_MACROS_GENERIC_DESTINATION"
    fi
fi

MAINSAIL_DESTINATION=/userdata/app/gk/printer_data/config/mainsail.cfg
if [ ! -f "$MAINSAIL_DESTINATION" ]; then
    cp mainsail.cfg "$MAINSAIL_DESTINATION"
fi

# Start Klippy
cd klippy

#Use tis klippy start line to enable logging for debugging
#nice -n -20 python -m klippy -a /tmp/unix_uds1 $CONFIG_DESTINATION >> /tmp/klippy.log 2>&1 &

#Silent start to avoid filling up log with normal output, as it consumes space in /tmp folder RAMDisk
nice -n -20 python -m klippy -a /tmp/unix_uds1 "$CONFIG_DESTINATION" >/dev/null 2>&1 &
assert_by_name klippy
