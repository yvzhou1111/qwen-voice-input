#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
import os
import time


def load_daemon():
    loader = importlib.machinery.SourceFileLoader("qvi", os.path.expanduser("~/.local/bin/qwen-voice-input"))
    spec = importlib.util.spec_from_loader("qvi", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main():
    daemon = load_daemon()
    env = daemon._env()
    daemon._ensure_state_dir()
    body = {
        "ts": time.time(),
        "tty": daemon._gnome_terminal_active_tty(env),
        "focus": daemon._atspi_focus_info(env),
    }
    tmp = daemon.TARGET_SNAPSHOT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(body, handle, ensure_ascii=False)
    os.replace(tmp, daemon.TARGET_SNAPSHOT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

