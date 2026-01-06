#!/bin/sh

# From a Windows machine:
#   docker run --rm -it -e KOBRA_MODEL_CODE="K3" -v .\build:/build -v .\files:/files -v .\apps:/apps ghcr.io/jbatonnet/rinkhals/build /build/build-swu.sh "APP_PATH"


if [ "$KOBRA_MODEL_CODE" = "" ]; then
    echo "Please specify your Kobra model using KOBRA_MODEL_CODE environment variable"
    exit 1
fi

set -e
BUILD_ROOT=$(dirname $(realpath $0))
. $BUILD_ROOT/tools.sh


# Prepare update
mkdir -p /tmp/update_swu
rm -rf /tmp/update_swu/*

cp /build/update.sh /tmp/update_swu/update.sh

APP_ROOT=$1
APP=$(basename $APP_ROOT)

if [ ! -f $APP_ROOT/app.sh ]; then
    echo "No app found in $APP_ROOT. Exiting SWU build"
    exit 1
fi

echo "Preparing update package for $APP..."

# Run get-*.sh scripts if present (to download dependencies for moonraker-obico or octoapp)
for GET_SCRIPT in $APP_ROOT/get-*.sh; do
    if [ -f "$GET_SCRIPT" ]; then
        echo "Running $(basename $GET_SCRIPT)..."
        chmod +x "$GET_SCRIPT"
        OBICO_DIRECTORY="$APP_ROOT" "$GET_SCRIPT" || echo "WARNING: $(basename $GET_SCRIPT) failed"
    fi
done

mkdir -p /tmp/update_swu/$APP
cp -r $APP_ROOT/* /tmp/update_swu/$APP/


# Create the update package
echo "Building update package..."

SWU_PATH=${2:-/build/dist/update.swu}
build_swu $KOBRA_MODEL_CODE /tmp/update_swu $SWU_PATH

echo "Done, your update package is ready: $SWU_PATH"
