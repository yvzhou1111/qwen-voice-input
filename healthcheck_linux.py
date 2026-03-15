#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SERVICE_NAME = "qwen-voice-input"
STATE_DIR = Path.home() / ".local" / "state" / SERVICE_NAME
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
SERVICE_FILE = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
MAX_HEARTBEAT_AGE = 120


def run(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def service_state():
    return run(["systemctl", "--user", "is-active", SERVICE_NAME]).stdout.strip()


def service_enabled():
    return run(["systemctl", "--user", "is-enabled", SERVICE_NAME]).stdout.strip() == "enabled"


def restart_service(reason):
    print(f"[healthcheck] restart {SERVICE_NAME}: {reason}")
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "restart", SERVICE_NAME], check=True)


def ensure_enabled():
    if SERVICE_FILE.exists() and not service_enabled():
        print(f"[healthcheck] enable {SERVICE_NAME}")
        run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)


def load_heartbeat():
    if not HEARTBEAT_PATH.exists():
        return None
    try:
        return json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ensure_enabled()

    state = service_state()
    if state != "active":
        restart_service(f"service_state={state or 'unknown'}")
        return 0

    heartbeat = load_heartbeat()
    if not heartbeat:
        restart_service("missing heartbeat")
        return 0

    age = time.time() - float(heartbeat.get("ts", 0))
    if age > MAX_HEARTBEAT_AGE:
        restart_service(f"stale heartbeat age={age:.1f}s")
        return 0

    print(f"[healthcheck] ok state={state} heartbeat_age={age:.1f}s pid={heartbeat.get('pid')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
