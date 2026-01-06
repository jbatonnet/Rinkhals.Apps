#!/usr/bin/env python3
"""Obico Initializer - runs once at startup to check link status and start linking if needed."""

import os
import re
import subprocess
import time
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
RINKHALS_HOME = os.environ.get("RINKHALS_HOME", "/useremain/home/rinkhals")
OBICO_CFG = f"{RINKHALS_HOME}/printer_data/config/moonraker-obico.cfg"
LINK_SH = f"{APP_ROOT}/link.sh"
LINK_LOG = f"{RINKHALS_HOME}/printer_data/logs/obico-link.log"


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {msg}", flush=True)


def get_app_property(prop: str) -> str:
    try:
        result = subprocess.run(
            [
                "sh",
                "-c",
                f". /useremain/rinkhals/.current/tools.sh && get_app_property obico {prop}",
            ],
            timeout=5,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def set_app_property(prop: str, value: str) -> None:
    try:
        subprocess.run(
            [
                "sh",
                "-c",
                f". /useremain/rinkhals/.current/tools.sh && set_app_property obico {prop} '{value}'",
            ],
            timeout=5,
            capture_output=True,
        )
    except Exception:
        pass


def is_linked() -> bool:
    if not os.path.exists(OBICO_CFG):
        return False
    with open(OBICO_CFG) as f:
        return "auth_token" in f.read()


def clear_auth_token() -> None:
    if not os.path.exists(OBICO_CFG):
        return
    with open(OBICO_CFG) as f:
        lines = [line for line in f if "auth_token" not in line]
    with open(OBICO_CFG, "w") as f:
        f.writelines(lines)
    log("Auth token cleared")


def check_relink() -> bool:
    relink = get_app_property("relink")
    if relink == "Yes":
        log("Relink requested")
        set_app_property("relink", "No")
        clear_auth_token()
        return True
    return False


def parse_link_code(text: str) -> str:
    clean = re.sub(r"(\x1b\[[0-9;?]*[a-zA-Z]|\[[\d;]*m)", "", text)
    match = re.search(r"manual linking and enter:\s+(\w{4,6})", clean)
    return match.group(1) if match else ""


def run_link() -> None:
    log("Starting link process...")
    set_app_property("is_linked", "Linking...")
    set_app_property("link_code", "Starting...")

    try:
        with open(LINK_LOG, "w") as log_file:
            proc = subprocess.Popen(
                ["sh", LINK_SH], stdin=subprocess.PIPE, stdout=log_file, stderr=log_file
            )
            last_code = ""
            start = time.time()

            while proc.poll() is None and (time.time() - start) < 1800:
                time.sleep(2)
                with open(LINK_LOG) as f:
                    code = parse_link_code(f.read())
                if code and code != last_code:
                    last_code = code
                    set_app_property("link_code", code)
                    set_app_property("is_linked", "Use code")
                    log(f"Link code: {code}")

            if proc.poll() is None:
                log("Link timeout, terminating...")
                proc.terminate()
            if proc.stdin:
                proc.stdin.close()
    except Exception as e:
        log(f"Link error: {e}")
        set_app_property("is_linked", "Error")
        return

    if is_linked():
        log("Linking successful!")
        set_app_property("is_linked", "Linked")
        set_app_property("link_code", "-")
    else:
        log("Linking incomplete")
        set_app_property("is_linked", "Not linked")


def main() -> None:
    log("Obico initializer starting...")

    if not os.path.exists(OBICO_CFG):
        log("No config found")
        set_app_property("is_linked", "No config")
        return

    relink_requested = check_relink()

    if is_linked() and not relink_requested:
        log("Already linked")
        set_app_property("is_linked", "Linked")
        set_app_property("link_code", "-")
        return

    run_link()
    log("Initializer complete")


if __name__ == "__main__":
    main()
