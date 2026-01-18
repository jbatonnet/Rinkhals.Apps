#!/bin/sh

# Run from Docker:
#   docker run --rm -it -v .\apps:/apps ghcr.io/jbatonnet/rinkhals/build /apps/vanilla-klipper/get-klipper.sh

mkdir /work
cd /work

# Klipper
echo "Downloading Klipper..."

wget -O klipper.zip https://github.com/Klipper3d/klipper/archive/refs/tags/v0.13.0.zip
unzip -d klipper klipper.zip

mkdir -p /apps/vanilla-klipper/klippy
rm -rf /apps/vanilla-klipper/klippy/*

cp -pr /work/klipper/*/klippy/* /apps/vanilla-klipper/klippy/
cp -p /work/klipper/*/scripts/klippy-requirements.txt /apps/vanilla-klipper/

cd /apps/vanilla-klipper
patch -p0 < klippy.patch

#Add driver for ACE Pro andvirtual_pins module
patch -p0 < ACEProDriver.patch

# Replace probe related code with newer version from klipper master, needed for kobra-s1
# This workaround can be removed when klipper > 0.13.0 is released and used here.
patch -p1 < v013_probe_update.patch

# Add new introduced lisd2w12, probe_ks1 and CS1237 extra modules
patch -p1 < anycubic_sensors.patch


