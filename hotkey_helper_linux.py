#!/usr/bin/env python3
import json
import os
import pwd
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from evdev import InputDevice, ecodes, list_devices

SERVICE_NAME = "qwen-voice-input.service"
CTRL_KEYS = {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL}
ALT_KEYS = {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT}
SPACE_KEYS = {ecodes.KEY_SPACE}
WATCH_KEYS = CTRL_KEYS | ALT_KEYS | SPACE_KEYS


def _target_user():
    user = os.environ.get("QWEN_VOICE_USER", "").strip()
    if user:
        return user
    # Best-effort fallback. Install script should set QWEN_VOICE_USER in the systemd unit.
    return "yilis"


def _user_info(user):
    info = pwd.getpwnam(user)
    return info.pw_uid, info.pw_dir


def _run_user_systemctl(user, uid, home_dir, *args):
    env = {
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
        "HOME": home_dir,
    }
    cmd = [
        "runuser",
        "-u",
        user,
        "--",
        "env",
        *[f"{k}={v}" for k, v in env.items()],
        "systemctl",
        "--user",
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _ensure_service_running(user, uid, home_dir):
    state = _run_user_systemctl(user, uid, home_dir, "is-active", SERVICE_NAME).stdout.strip()
    if state == "active":
        return True, False

    _run_user_systemctl(user, uid, home_dir, "start", SERVICE_NAME)
    deadline = time.time() + 5
    while time.time() < deadline:
        state = _run_user_systemctl(user, uid, home_dir, "is-active", SERVICE_NAME).stdout.strip()
        if state == "active":
            return True, True
        time.sleep(0.2)
    return state == "active", True


def _heartbeat_ready(heartbeat_path: Path):
    try:
        state = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        return state.get("busy") in {False, None}
    except Exception:
        return False


def _send_signal(user, uid, home_dir, heartbeat_path: Path, sig_name: str):
    ok, started_now = _ensure_service_running(user, uid, home_dir)
    if not ok:
        return
    # If we had to start the service just now, wait for it to finish booting and
    # drop the current trigger to avoid killing it before signal handlers are ready.
    if started_now:
        deadline = time.time() + 5
        while time.time() < deadline:
            if _heartbeat_ready(heartbeat_path):
                break
            time.sleep(0.1)
        return
    _run_user_systemctl(user, uid, home_dir, "kill", "--kill-whom=main", "-s", sig_name, SERVICE_NAME)


def _should_watch(device: InputDevice) -> bool:
    try:
        name = (device.name or "").lower()
        if "keyboard" not in name and "translated set 2" not in name and "kbd" not in name:
            return False
        caps = device.capabilities()
        keys = set(caps.get(ecodes.EV_KEY, []))
        return bool(keys & CTRL_KEYS) and bool(keys & ALT_KEYS) and bool(keys & SPACE_KEYS)
    except Exception:
        return False


def _open_devices():
    devices = {}
    for path in list_devices():
        try:
            dev = InputDevice(path)
            if _should_watch(dev):
                devices[dev.fd] = dev
        except Exception:
            continue
    return devices


def main():
    user = _target_user()
    try:
        uid, home_dir = _user_info(user)
    except KeyError:
        print(f"unknown user: {user}", file=sys.stderr)
        return 2

    heartbeat = Path(home_dir) / ".local" / "state" / "qwen-voice-input" / "heartbeat.json"

    devices = _open_devices()
    if not devices:
        print("no keyboard-like input devices found", file=sys.stderr)
        return 1

    pressed = set()
    recording = False

    def shutdown(_sig, _frame):
        for dev in devices.values():
            try:
                dev.close()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            readable, _, _ = select.select(list(devices.values()), [], [], 2.0)
        except Exception:
            devices = _open_devices()
            continue

        if not readable:
            continue

        for dev in readable:
            try:
                for event in dev.read():
                    if event.type != ecodes.EV_KEY or event.code not in WATCH_KEYS:
                        continue
                    if event.value == 1:
                        pressed.add(event.code)
                    elif event.value == 0:
                        pressed.discard(event.code)
                    elif event.value == 2:
                        continue

                    combo_down = bool(pressed & CTRL_KEYS) and bool(pressed & ALT_KEYS) and bool(pressed & SPACE_KEYS)
                    if combo_down and not recording:
                        _send_signal(user, uid, home_dir, heartbeat, "USR1")
                        recording = True
                    elif recording and not combo_down:
                        _send_signal(user, uid, home_dir, heartbeat, "USR2")
                        recording = False
            except OSError:
                devices = _open_devices()
                break


if __name__ == "__main__":
    raise SystemExit(main())

