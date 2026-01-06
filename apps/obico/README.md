# Obico App for Rinkhals

[Obico](https://www.obico.io) (formerly The Spaghetti Detective) for Anycubic Kobra printers with Rinkhals firmware. AI-powered failure detection and remote monitoring.

## Features

- **AI Failure Detection**: Automatically detects print failures
- **Remote Monitoring**: Monitor prints via web/mobile app
- **HD Webcam Streaming**: Up to 25 FPS
- **Layer Tracking**: Via Klipper macros

## Compatibility

| Feature | Status | Notes |
|---------|--------|-------|
| AI failure detection | ✅ Works | Uses webcam |
| Remote monitoring | ✅ Works | Via Obico tunnel |
| Print status tracking | ✅ Works | Via Moonraker API |
| Layer tracking | ✅ Works | Patched for GoKlipper |
| MJPEG streaming | ✅ Works | Standard webcam feed |
| WebRTC streaming | ❌ N/A | Janus not compatible (see below) |
| First-layer AI scan | ❌ N/A | Requires Jinja2 templating |

### WebRTC/Janus Limitation

The Kobra S1 uses **uClibc** (embedded C library) instead of glibc. Janus WebRTC binaries are compiled for glibc systems (Debian/Raspberry Pi) and **will not work** on the Kobra S1.

**Impact:** Obico falls back to **MJPEG streaming**, which still provides a camera feed but with slightly higher latency than WebRTC. AI failure detection and all other features work normally.

## Tested Printers
- Anycubic Kobra S1 Combo

## Installation

### Via SWU Package (Recommended)

1. Download `update.swu` from releases
2. Copy to USB drive in folder `aGVscF9zb3Nf`
3. Insert USB, wait for two beeps
4. Enable via Rinkhals UI or create `/useremain/home/rinkhals/apps/obico.enabled`
5. Restart printer
6. **Wait up to 5 minutes** for dependencies to install

### Manual Installation

```bash
ssh root@PRINTER_IP  # password: rockchip
cd /useremain/home/rinkhals/apps
# Copy obico folder here
cd obico
./install.sh
touch /useremain/home/rinkhals/apps/obico.enabled
# Restart printer
```

## Linking to Obico

### Via Touchscreen UI (Recommended)

1. After installation completes, restart the printer
2. Open Rinkhals settings → Apps → Obico → Settings
3. A **Link code** will appear automatically (if printer is unlinked)
4. Scan the Link code with your phone
5. Complete linking in the Obico app

**To relink:** Set **"Relink"** to "Yes" → restart printer → new Link code appears.

### Via SSH

```bash
ssh root@PRINTER_IP
/useremain/home/rinkhals/apps/obico/link.sh
```

## Re-linking

### Via Touchscreen (Recommended)
1. Go to Apps → Obico → Settings
2. Set **"Relink"** to "Yes"
3. Restart printer
4. New Link code will appear

### Via SSH
```bash
/useremain/home/rinkhals/apps/obico/link.sh --relink
```

## Layer Tracking

Add to slicer's **"Before layer change G-code"**:

**PrusaSlicer/OrcaSlicer:**
```gcode
SET_GCODE_VARIABLE MACRO=_OBICO_LAYER_CHANGE VARIABLE=current_layer VALUE=[layer_num]
```

**Cura:**
```gcode
SET_GCODE_VARIABLE MACRO=_OBICO_LAYER_CHANGE VARIABLE=current_layer VALUE={layer_nr}
```

## Configuration

Config file: `/useremain/home/rinkhals/printer_data/config/moonraker-obico.cfg`

## Troubleshooting

### View Logs
```bash
tail -f /useremain/home/rinkhals/printer_data/logs/moonraker-obico.log
tail -f /useremain/home/rinkhals/printer_data/logs/obico-app.log
```

### Verify Moonraker
```bash
curl http://127.0.0.1:7125/printer/info
```

### Re-run Installation
```bash
cd /useremain/home/rinkhals/apps/obico
./install.sh
./app.sh start
```

## Uninstallation

```bash
rm /useremain/home/rinkhals/apps/obico.enabled
touch /useremain/home/rinkhals/apps/obico.disabled
/useremain/home/rinkhals/apps/obico/app.sh stop
# Optional: rm -rf /useremain/home/rinkhals/apps/obico
```

## Links

- [Obico Documentation](https://www.obico.io/docs/)
- [Obico GitHub](https://github.com/TheSpaghettiDetective/moonraker-obico)
- [Rinkhals](https://github.com/jbatonnet/Rinkhals)
