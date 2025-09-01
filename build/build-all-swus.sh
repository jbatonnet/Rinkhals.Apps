#!/bin/sh

# From a Windows machine:
#   docker run --rm -it -v .\build:/build -v .\files:/files -v .\apps:/apps ghcr.io/jbatonnet/rinkhals/build /build/build-all-swus.sh


set -e
BUILD_ROOT=$(dirname $(realpath $0))
. $BUILD_ROOT/tools.sh


mkdir -p /tmp/update_swu

for APP_ROOT in $(find /apps -type d -mindepth 1 -maxdepth 1); do
    if [ ! -f $APP_ROOT/app.sh ]; then
        echo "No app found in $APP_ROOT. Skipping"
        continue
    fi
    
    APP=$(basename $APP_ROOT)
    echo "Preparing app package for $APP..."

    # Prepare update
    rm -rf /tmp/update_swu/*
    cp /build/update.sh /tmp/update_swu/update.sh
    mkdir -p /tmp/update_swu/$APP
    cp -r $APP_ROOT/* /tmp/update_swu/$APP/

    # Create the update package
    SWU_PATH=${2:-/build/dist/update.swu}
    build_swu K3 /tmp/update_swu /build/dist/app-$APP-k2p-k3.swu
    build_swu KS1 /tmp/update_swu /build/dist/app-$APP-ks1.swu
    build_swu K3M /tmp/update_swu /build/dist/app-$APP-k3m.swu
done

echo "Done, your app packages are ready: /build/dist/app-*.swu"
