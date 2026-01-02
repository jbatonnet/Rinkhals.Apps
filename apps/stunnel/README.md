# TLS for Rinkhals

This adds HTTPS endpoints multiple HTTP services of rinkhals. For which ports are mapped to where, please consult
[stunnel.conf](./stunnel.conf). It uses self-signed certificated which get created on the fly if necessary.
See [rinkhals_ssl.conf](./rinkhals_ssl.conf) for details. 

> **NOTE:** If one is browsing the HTTPS endpoints the browser will most likely show a warning. It should be read 
> carefully! If the implications are acceptable, proceed with trusting the proposed certificated.

## Installation

### Using the SWU Package

1. Download the `app-stunnel.swu` file from GitHub Actions.
2. Copy it to a FAT32-formatted USB drive inside a folder named `aGVscF9zb3Nf`.
3. Plug the USB drive into your Kobra printer.
4. Wait for two beeps (the second beep confirms the installation).
5. Enable the application via the Rinkhals touchscreen interface or by creating the file `/useremain/home/rinkhals/apps/stunnel.enabled`.
6. Restart your printer.
