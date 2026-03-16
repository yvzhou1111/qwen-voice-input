#!/usr/bin/env python3
import base64
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import wave
from pathlib import Path


def load_daemon():
    loader = importlib.machinery.SourceFileLoader("qvi", str(Path.home() / ".local/bin/qwen-voice-input"))
    spec = importlib.util.spec_from_loader("qvi", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_env():
    env_path = Path.home() / ".config" / "qwen-voice-input.env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)


def check_service_states():
    names = {
        "user_service": ["systemctl", "--user", "is-active", "qwen-voice-input.service"],
        "hotkey_service": ["systemctl", "is-active", "qwen-voice-input-hotkey@yilis.service"],
        "ydotoold_service": ["systemctl", "is-active", "qwen-voice-input-ydotoold.service"],
    }
    results = {}
    ok = True
    for key, cmd in names.items():
        state = run(cmd).stdout.strip()
        results[key] = state
        ok = ok and state == "active"
    return ok, results


def check_backend_api(env):
    try:
        import numpy as np
        from openai import OpenAI
    except Exception as exc:
        return False, {"error": f"missing dependency: {exc}"}

    api_key = env.get("DASHSCOPE_API_KEY") or env.get("QWEN_VOICE_API_KEY")
    base_url = env.get("QWEN_VOICE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    model = env.get("QWEN_VOICE_REMOTE_MODEL", "qwen3-asr-flash")
    language = env.get("QWEN_VOICE_LANGUAGE", "zh")
    if not api_key:
        return False, {"error": "missing DASHSCOPE_API_KEY"}

    sr = 16000
    samples = np.zeros(sr, dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        wav_file.writeframes(samples.tobytes())
    audio_uri = "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": audio_uri},
                }],
            }],
            stream=False,
            extra_body={"asr_options": {"enable_itn": True, "language": language}},
        )
        _ = resp.choices[0].message.content
        return True, {"model": model, "base_url": base_url}
    except Exception as exc:
        return False, {"error": str(exc), "model": model, "base_url": base_url}


def check_gui_route():
    daemon = load_daemon()
    tmpdir = Path(tempfile.mkdtemp(prefix="qvi-selftest-"))
    out_path = tmpdir / "gui.txt"
    script_path = tmpdir / "gtk_entry.py"
    script_path.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env /usr/bin/python3
        import gi
        from pathlib import Path
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, GLib
        out = Path({str(out_path)!r})
        out.write_text('', encoding='utf-8')
        win = Gtk.Window(title='QviSelftestGui')
        win.set_default_size(420, 80)
        entry = Gtk.Entry()
        win.add(entry)
        win.show_all()
        def focus_it():
            win.present()
            entry.grab_focus()
            return False
        def finish():
            out.write_text(entry.get_text(), encoding='utf-8')
            Gtk.main_quit()
            return False
        GLib.timeout_add(300, focus_it)
        GLib.timeout_add(5000, finish)
        Gtk.main()
    """), encoding="utf-8")
    proc = subprocess.Popen(
        ["/usr/bin/python3", str(script_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1)
        daemon.type_text("GUI_ROUTE_OK")
        time.sleep(6)
        value = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        return value == "GUI_ROUTE_OK", {"value": value}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def check_active_codex_route():
    daemon = load_daemon()
    env = daemon._env()
    active_tty = daemon._gnome_terminal_active_tty(env)
    if not active_tty:
        return False, {"error": "no active gnome-terminal tty"}

    codex_pid = run(
        ["bash", "-lc", f"ps -eo pid=,tty=,comm= | awk '$2==\"{active_tty}\" && $3==\"codex\" {{print $1; exit}}'"]
    ).stdout.strip()
    if not codex_pid:
        return False, {"error": f"no codex process on {active_tty}"}

    log_base = Path(tempfile.mkdtemp(prefix="qvi-selftest-")) / "codex_strace.log"
    strace = subprocess.Popen(
        ["sudo", "strace", "-ff", "-tt", "-e", "read", "-p", codex_pid, "-o", str(log_base)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1)
        daemon.type_text("QVI_ACTIVE_OK")
        time.sleep(3)
    finally:
        strace.terminate()
        try:
            strace.wait(timeout=2)
        except Exception:
            strace.kill()

    reads = []
    for path in log_base.parent.glob(log_base.name + "*"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 'read(0, "Q"' in text and 'read(0, "K"' in text:
            reads.append(path.name)
    return bool(reads), {"active_tty": active_tty, "codex_pid": codex_pid, "trace_files": reads}


def main():
    env = load_env()
    report = {}

    ok, data = check_service_states()
    report["services"] = {"ok": ok, **data}

    ok, data = check_backend_api(env)
    report["backend_api"] = {"ok": ok, **data}

    ok, data = check_gui_route()
    report["gui_route"] = {"ok": ok, **data}

    ok, data = check_active_codex_route()
    report["active_codex_route"] = {"ok": ok, **data}

    overall = all(item.get("ok") for item in report.values())
    print(json.dumps({"ok": overall, "checks": report}, ensure_ascii=False, indent=2))
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())

