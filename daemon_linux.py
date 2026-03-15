#!/usr/bin/env python3
"""
Qwen3-ASR 语音输入守护进程 - Linux
快捷键: Ctrl+Alt+Space 按住录音，松开转文字并输入
"""

import base64
import io
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import warnings
import wave
import numpy as np
import sounddevice as sd
import scipy.signal as sps

warnings.filterwarnings("ignore", message=".*RequestsDependencyWarning.*")

# ── 配置 ──────────────────────────────────────────────
LOCAL_MODEL_NAME = "Qwen/Qwen3-ASR-0.6B"
ASR_BACKEND = os.environ.get("QWEN_VOICE_BACKEND", "local").strip().lower()
REMOTE_MODEL_NAME = os.environ.get("QWEN_VOICE_REMOTE_MODEL", "qwen3-asr-flash").strip() or "qwen3-asr-flash"
REMOTE_BASE_URL = os.environ.get(
    "QWEN_VOICE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"
REMOTE_LANGUAGE = os.environ.get("QWEN_VOICE_LANGUAGE", "").strip()
REMOTE_ENABLE_ITN = os.environ.get("QWEN_VOICE_ENABLE_ITN", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
REMOTE_TIMEOUT = float(os.environ.get("QWEN_VOICE_TIMEOUT", "60"))
GUI_INPUT_MODE = os.environ.get("QWEN_VOICE_GUI_INPUT_MODE", "auto").strip().lower()
RECORD_RATE = 44100
TARGET_RATE = 16000
CHANNELS    = 1
HOTKEY      = {"ctrl", "alt", "space"}
SUPPORTED_SHELLS = {"bash", "zsh", "fish"}
DEDICATED_TERMINAL_TITLE = "Qwen Voice Input"
STATE_DIR = os.path.expanduser("~/.local/state/qwen-voice-input")
HEARTBEAT_PATH = os.path.join(STATE_DIR, "heartbeat.json")
ATSPI_HELPER = os.environ.get(
    "QWEN_VOICE_ATSPI_HELPER",
    os.path.expanduser("~/.local/bin/qwen-voice-input-atspi"),
).strip()
YDOTOOL_SOCKET = os.environ.get(
    "QWEN_VOICE_YDOTOOL_SOCKET",
    "/tmp/.ydotool_socket",
).strip() or "/tmp/.ydotool_socket"
# ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)
_PYNPUT_KEYBOARD = None


def _keyboard_module():
    global _PYNPUT_KEYBOARD
    if _PYNPUT_KEYBOARD is None:
        from pynput import keyboard as pynput_keyboard
        _PYNPUT_KEYBOARD = pynput_keyboard
    return _PYNPUT_KEYBOARD


def _env():
    return {**os.environ,
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")}


def _ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)


def _write_heartbeat(payload=None):
    _ensure_state_dir()
    body = {
        "ts": time.time(),
        "pid": os.getpid(),
    }
    if payload:
        body.update(payload)
    tmp = f"{HEARTBEAT_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(body, handle, ensure_ascii=False)
    os.replace(tmp, HEARTBEAT_PATH)


def _normalize_key(key):
    keyboard = _keyboard_module()
    if key in {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}:
        return "ctrl"
    if key in {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr}:
        return "alt"
    if key == keyboard.Key.space:
        return "space"
    try:
        if getattr(key, "char", None) == " ":
            return "space"
    except Exception:
        pass
    return key


def _run(cmd, env=None, timeout=3, check=False, input_data=None):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=check,
        input=input_data,
    )


def _session_type():
    return os.environ.get("XDG_SESSION_TYPE", "").strip().lower()


def _audio_to_data_uri(audio, sample_rate):
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16.tobytes())
    payload = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:audio/wav;base64,{payload}"


def _window_name(window_id, env):
    return _run(["xdotool", "getwindowname", str(window_id)], env=env).stdout.strip()


def _window_class(window_id, env):
    try:
        output = _run(["xprop", "-id", str(window_id), "WM_CLASS"], env=env).stdout
    except Exception:
        return []

    if "=" not in output:
        return []

    parts = output.split("=", 1)[1].split(",")
    return [p.strip().strip('"').lower() for p in parts if p.strip()]


def _is_terminal_window(window_id, env):
    name = _window_name(window_id, env).lower()
    classes = _window_class(window_id, env)
    if any(token in name for token in ["terminal", "konsole", "xterm", "bash", "zsh", "fish", "@", "终端"]):
        return True
    return any(token in " ".join(classes) for token in [
        "terminal", "gnome-terminal", "alacritty", "kitty", "konsole", "tilix", "xfce4-terminal"
    ])


def _active_x11_window_id(env):
    try:
        window_id = _run(["xdotool", "getactivewindow"], env=env, timeout=2).stdout.strip()
        return window_id or None
    except Exception:
        return None


def _atspi_focus_info(env):
    helper_path = os.path.expanduser(ATSPI_HELPER) if ATSPI_HELPER else ""
    if not helper_path or not os.path.exists(helper_path):
        return None
    try:
        proc = subprocess.run(
            [helper_path, "focus-info"],
            capture_output=True,
            text=True,
            env=env,
            timeout=2,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout.strip() or "{}")
    except Exception:
        return None


def _atspi_insert(env, text):
    helper_path = os.path.expanduser(ATSPI_HELPER) if ATSPI_HELPER else ""
    if not helper_path or not os.path.exists(helper_path):
        return False
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    try:
        proc = subprocess.run(
            [helper_path, "insert", payload],
            capture_output=True,
            text=True,
            env=env,
            timeout=3,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _type_with_ydotool(text, env):
    if not shutil.which("ydotool"):
        return False

    safe_text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if not safe_text:
        return True

    env = {
        **env,
        "YDOTOOL_SOCKET": YDOTOOL_SOCKET,
    }
    try:
        subprocess.run(
            ["ydotool", "type", "--key-delay", "0", "--file", "-"],
            input=safe_text,
            text=True,
            env=env,
            timeout=10,
            check=True,
            capture_output=True,
        )
        log.info("已通过 ydotool 注入")
        return True
    except Exception as exc:
        log.warning("ydotool 注入失败: %s", exc)
        return False


def _iter_process_rows():
    output = _run(["ps", "-eo", "pid=,ppid=,tty=,comm=,args="], timeout=5).stdout
    rows = []
    for line in output.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 4:
            continue
        pid, ppid, tty, comm = parts[:4]
        args = parts[4] if len(parts) > 4 else comm
        try:
            stat = _run(["ps", "-o", "pgid=,tpgid=", "-p", pid], timeout=3).stdout.strip().split()
            pgid = int(stat[0]) if len(stat) > 0 else -1
            tpgid = int(stat[1]) if len(stat) > 1 else -1
            rows.append({
                "pid": int(pid),
                "ppid": int(ppid),
                "pgid": pgid,
                "tpgid": tpgid,
                "tty": tty,
                "comm": comm,
                "args": args,
            })
        except ValueError:
            continue
    return rows


def _descendants(root_pid):
    rows = _iter_process_rows()
    by_parent = {}
    for row in rows:
        by_parent.setdefault(row["ppid"], []).append(row)

    stack = [root_pid]
    result = []
    while stack:
        current = stack.pop()
        for child in by_parent.get(current, []):
            result.append(child)
            stack.append(child["pid"])
    return result


def _cwd_of(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _tty_index_from_name(tty_name):
    if not tty_name.startswith("pts/"):
        return None
    try:
        return int(tty_name.split("/", 1)[1])
    except ValueError:
        return None


def _master_fd_by_tty_index(server_pid):
    mapping = {}
    fdinfo_dir = f"/proc/{server_pid}/fdinfo"
    if not os.path.isdir(fdinfo_dir):
        return mapping

    for fd_name in os.listdir(fdinfo_dir):
        fdinfo_path = os.path.join(fdinfo_dir, fd_name)
        try:
            with open(fdinfo_path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            continue

        tty_index = None
        for line in content.splitlines():
            if line.startswith("tty-index:"):
                try:
                    tty_index = int(line.split(":", 1)[1].strip())
                except ValueError:
                    tty_index = None
                break

        if tty_index is not None:
            mapping[tty_index] = int(fd_name)

    return mapping


def _select_shell_for_window(server_pid, window_title):
    candidates = []
    home = os.path.expanduser("~")
    host = socket.gethostname().lower()
    title = (window_title or "").lower()

    descendants = _descendants(server_pid)

    def consider(proc):
        tty_index = _tty_index_from_name(proc["tty"])
        if tty_index is None or proc["comm"] not in SUPPORTED_SHELLS:
            return

        cwd = _cwd_of(proc["pid"])
        cwd_lower = cwd.lower()
        basename = os.path.basename(cwd_lower)
        home_form = cwd_lower.replace(home.lower(), "~", 1) if cwd_lower.startswith(home.lower()) else cwd_lower

        score = 0
        if cwd_lower and cwd_lower in title:
            score += 60
        if home_form and home_form in title:
            score += 80
        if basename and basename in title:
            score += 30
        if host in title:
            score += 5

        candidates.append({
            "pid": proc["pid"],
            "pgid": proc.get("pgid"),
            "tpgid": proc.get("tpgid"),
            "tty_index": tty_index,
            "cwd": cwd,
            "score": score,
        })

    # Prefer shells directly spawned by the terminal server.
    for proc in descendants:
        if proc.get("ppid") == server_pid:
            consider(proc)

    # Some terminal implementations add intermediate processes. Fall back to any descendant.
    if not candidates:
        for proc in descendants:
            consider(proc)

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item["score"], item["pid"]), reverse=True)
    return candidates[0]


def _shell_is_idle(shell_info):
    return shell_info["pid"] > 0 and shell_info.get("pgid") == shell_info["pid"] and shell_info.get("tpgid") == shell_info["pid"]


def _list_terminal_windows(env):
    return [wid for wid in _run(["xdotool", "search", "--class", "gnome-terminal"], env=env, timeout=3).stdout.split() if wid.strip()]


def _find_idle_terminal_window(env):
    candidates = []
    for window_id in _list_terminal_windows(env):
        try:
            server_pid = _run(["xdotool", "getwindowpid", str(window_id)], env=env).stdout.strip()
            if not server_pid:
                continue
            shell = _select_shell_for_window(int(server_pid), _window_name(window_id, env))
            if shell and _shell_is_idle(shell):
                name = _window_name(window_id, env)
                dedicated = 1 if DEDICATED_TERMINAL_TITLE.lower() in name.lower() else 0
                candidates.append((dedicated, shell["pid"], window_id, shell))
        except Exception:
            continue

    if not candidates:
        return None, None

    candidates.sort(reverse=True)
    _, _, window_id, shell = candidates[0]
    return window_id, shell


def _spawn_terminal_window(env):
    existing = set(_list_terminal_windows(env))
    subprocess.Popen(
        ["gnome-terminal", f"--title={DEDICATED_TERMINAL_TITLE}", "--", "bash", "-lc", "exec bash"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 8
    while time.time() < deadline:
        for window_id in _list_terminal_windows(env):
            if window_id in existing:
                continue
            name = _window_name(window_id, env)
            if DEDICATED_TERMINAL_TITLE.lower() not in name.lower():
                continue
            server_pid = _run(["xdotool", "getwindowpid", str(window_id)], env=env).stdout.strip()
            if not server_pid:
                continue
            shell = _select_shell_for_window(int(server_pid), name)
            if shell and _shell_is_idle(shell):
                return window_id, shell
        time.sleep(0.2)

    return _find_idle_terminal_window(env)


def _resolve_target_window(requested_window, env):
    if requested_window:
        return requested_window

    window_id = _run(["xdotool", "getactivewindow"], env=env, timeout=2).stdout.strip()
    if window_id:
        return window_id

    raise RuntimeError("未找到活动窗口")


def _inject_into_terminal_by_pid(server_pid, window_title, text):
    shell = _select_shell_for_window(int(server_pid), window_title)
    if shell is None:
        raise RuntimeError("无法定位终端标签页对应的 shell")

    master_fds = _master_fd_by_tty_index(int(server_pid))
    master_fd = master_fds.get(shell["tty_index"])
    if master_fd is None:
        raise RuntimeError(f"无法定位 pts/{shell['tty_index']} 对应的终端主设备")

    payload = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    fd = os.open(f"/proc/{server_pid}/fd/{master_fd}", os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)

    log.info("已写入终端 pts/%s (%s)", shell["tty_index"], shell["cwd"] or "unknown")


def _inject_into_gnome_terminal(window_id, text, env):
    server_pid = _run(["xdotool", "getwindowpid", str(window_id)], env=env).stdout.strip()
    if not server_pid:
        raise RuntimeError("无法获取终端进程 PID")
    window_title = _window_name(window_id, env)
    _inject_into_terminal_by_pid(int(server_pid), window_title, text)


def _type_with_xdotool(window_id, text, env):
    safe_text = text.replace("\r", " ").replace("\n", " ")
    subprocess.run(
        ["xdotool", "windowactivate", "--sync", str(window_id)],
        env=env,
        timeout=5,
        check=True,
    )
    subprocess.run(
        ["xdotool", "type", "--delay", "0", "--clearmodifiers", "--window", str(window_id), safe_text],
        env=env,
        timeout=10,
        check=True,
    )


def type_text(text, target_window=None, target_context=None):
    env = _env()
    payload = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if not payload.strip():
        return
    try:
        if _session_type() == "wayland":
            active_x11_window = _active_x11_window_id(env)
            if active_x11_window:
                win_name = _window_name(active_x11_window, env).lower()
                win_classes = _window_class(active_x11_window, env)
                log.info("目标窗口(X11): %s classes=%s", win_name, ",".join(win_classes) or "unknown")
                is_terminal = _is_terminal_window(active_x11_window, env)
                is_gnome_terminal = any(token in win_classes for token in ["gnome-terminal", "gnome-terminal-server"])
                if is_terminal and is_gnome_terminal:
                    _inject_into_gnome_terminal(active_x11_window, payload, env)
                else:
                    _type_with_xdotool(active_x11_window, payload, env)
                return

            ctx = target_context or _atspi_focus_info(env) or {}
            if ctx.get("terminal_like") and ctx.get("pid"):
                try:
                    _inject_into_terminal_by_pid(int(ctx["pid"]), ctx.get("window_title") or "", payload)
                    return
                except Exception as e:
                    log.warning("Wayland 终端直写失败: %s", e)

            if ctx and not ctx.get("terminal_like") and not ctx.get("focus_editable") and not ctx.get("textish"):
                raise RuntimeError(
                    f"当前焦点不是输入框 (app={ctx.get('app_name')}, role={ctx.get('focus_role')})"
                )

            if _atspi_insert(env, payload):
                log.info("已通过 AT-SPI 注入")
                return

            if _type_with_ydotool(payload, env):
                return

            raise RuntimeError("Wayland 注入失败 (AT-SPI/ydotool 均未成功)")

        if not target_window:
            target_window = _active_x11_window_id(env) or ""

        target_window = _resolve_target_window(target_window, env)
        if not target_window:
            raise RuntimeError("未找到活动窗口")

        win_name = _window_name(target_window, env).lower()
        win_classes = _window_class(target_window, env)
        log.info("目标窗口: %s classes=%s", win_name, ",".join(win_classes) or "unknown")

        is_terminal = _is_terminal_window(target_window, env)
        is_gnome_terminal = any(token in win_classes for token in ["gnome-terminal", "gnome-terminal-server"])
        if is_terminal and is_gnome_terminal:
            try:
                _inject_into_gnome_terminal(target_window, payload, env)
                return
            except Exception as e:
                log.warning("终端直写失败，改用 xdotool type: %s", e)

        _type_with_xdotool(target_window, payload, env)
    except Exception as e:
        log.error("type_text error: %s", e)


def _resolve_model_path():
    try:
        from huggingface_hub import snapshot_download
    except Exception:
        return LOCAL_MODEL_NAME

    try:
        model_dir = snapshot_download(LOCAL_MODEL_NAME, local_files_only=True)
        log.info("使用本地模型缓存: %s", model_dir)
        return model_dir
    except Exception as e:
        log.warning("本地模型缓存未命中，将尝试联网下载: %s", e)
        model_dir = snapshot_download(LOCAL_MODEL_NAME)
        log.info("模型已缓存到: %s", model_dir)
        return model_dir


def _pick_input_device():
    try:
        info = sd.query_devices("default", "input")
        if info["max_input_channels"] > 0:
            return int(info["index"]), info
    except Exception:
        pass

    devices = sd.query_devices()
    default_device = sd.default.device

    try:
        input_device = int(default_device[0])
        if input_device >= 0:
            info = sd.query_devices(input_device, "input")
            if info["max_input_channels"] > 0:
                return input_device, info
    except Exception:
        pass

    for idx, info in enumerate(devices):
        if info["max_input_channels"] > 0:
            return idx, info

    raise RuntimeError("未找到可用的录音设备")


class VoiceInputDaemon:
    def __init__(self):
        self.model         = None
        self.backend       = ASR_BACKEND
        self.recording     = False
        self.audio_buf     = []
        self.pressed       = set()
        self.lock          = threading.Lock()
        self.stream        = None
        self._busy         = False
        self.target_window = None
        self.target_context = None
        self.record_rate   = RECORD_RATE
        self._stop_event   = threading.Event()

    def _heartbeat_loop(self):
        while not self._stop_event.wait(10):
            try:
                _write_heartbeat({
                    "recording": self.recording,
                    "busy": self._busy,
                    "target_window": self.target_window,
                    "target_context": self.target_context,
                })
            except Exception as e:
                log.warning("写入心跳失败: %s", e)

    def _load_remote_client(self):
        api_key = os.environ.get("QWEN_VOICE_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            log.error("未配置 DASHSCOPE_API_KEY 或 QWEN_VOICE_API_KEY")
            sys.exit(1)

        try:
            from openai import OpenAI
        except Exception as e:
            log.error("openai SDK 导入失败: %s", e)
            sys.exit(1)

        self.model = OpenAI(api_key=api_key, base_url=REMOTE_BASE_URL, timeout=REMOTE_TIMEOUT)
        log.info("后端: dashscope_openai")
        log.info("远程模型: %s", REMOTE_MODEL_NAME)
        log.info("Base URL: %s", REMOTE_BASE_URL)
        if REMOTE_LANGUAGE:
            log.info("语言提示: %s", REMOTE_LANGUAGE)
        log.info("ITN: %s", REMOTE_ENABLE_ITN)

    def load_model(self):
        if self.backend == "dashscope_openai":
            self._load_remote_client()
            return

        if self.backend != "local":
            log.error("不支持的 ASR 后端: %s", self.backend)
            sys.exit(1)

        log.info("后端: local")
        log.info("正在加载模型 %s ...", LOCAL_MODEL_NAME)
        try:
            import torch
            from qwen_asr import Qwen3ASRModel

            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            dtype  = torch.float16 if torch.cuda.is_available() else torch.float32
            log.info("设备: %s  dtype: %s", device, dtype)

            self.model = Qwen3ASRModel.from_pretrained(
                _resolve_model_path(), dtype=dtype, device_map=device, max_new_tokens=128
            )

            if torch.cuda.is_available():
                try:
                    self.model.model = torch.compile(self.model.model, mode="reduce-overhead")
                    log.info("torch.compile 已启用")
                except Exception as ce:
                    log.warning("torch.compile 跳过: %s", ce)

            dummy = np.zeros(TARGET_RATE, dtype=np.float32)
            self.model.transcribe((dummy, TARGET_RATE), language=None)
            log.info("模型加载完成（已预热）")
        except Exception as e:
            log.error("模型加载失败: %s", e)
            sys.exit(1)

    def _transcribe_remote(self, audio):
        request = {
            "model": REMOTE_MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": _audio_to_data_uri(audio, TARGET_RATE)
                            },
                        }
                    ],
                }
            ],
            "stream": False,
            "extra_body": {
                "asr_options": {
                    "enable_itn": REMOTE_ENABLE_ITN,
                }
            },
        }
        if REMOTE_LANGUAGE:
            request["extra_body"]["asr_options"]["language"] = REMOTE_LANGUAGE

        completion = self.model.chat.completions.create(**request)
        message = completion.choices[0].message.content
        if isinstance(message, str):
            return message.strip()
        if isinstance(message, list):
            parts = []
            for item in message:
                if isinstance(item, str):
                    parts.append(item)
                elif hasattr(item, "text") and item.text:
                    parts.append(item.text)
                elif isinstance(item, dict):
                    if item.get("text"):
                        parts.append(item["text"])
                    elif item.get("content"):
                        parts.append(item["content"])
            return "".join(parts).strip()
        return str(message).strip()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning("音频回调状态: %s", status)
        if self.recording:
            self.audio_buf.append(indata.copy())

    def start_recording(self):
        with self.lock:
            if self.recording or self._busy:
                return
            self.recording = True
            self.audio_buf = []
            try:
                env = _env()
                if _session_type() == "wayland":
                    self.target_window = None
                    self.target_context = _atspi_focus_info(env)
                else:
                    self.target_window = subprocess.run(
                        ["xdotool", "getactivewindow"],
                        capture_output=True, text=True, env=env, timeout=2
                    ).stdout.strip()
                    self.target_context = None
            except Exception:
                self.target_window = None
                self.target_context = None
            _write_heartbeat({
                "recording": True,
                "busy": self._busy,
                "target_window": self.target_window,
                "target_context": self.target_context,
            })
        log.info("开始录音，目标窗口: %s", self.target_window)

    def stop_recording(self):
        with self.lock:
            if not self.recording:
                return
            self.recording = False
            buf = list(self.audio_buf)
            _write_heartbeat({
                "recording": False,
                "busy": True,
                "target_window": self.target_window,
                "target_context": self.target_context,
            })
        threading.Thread(target=self._transcribe, args=(buf, self.target_window, self.target_context), daemon=True).start()

    def _transcribe(self, buf, target_window=None, target_context=None):
        self._busy = True
        try:
            if len(buf) < 5:
                log.info("录音过短，已忽略")
                return
            audio = np.concatenate(buf, axis=0).flatten().astype(np.float32)
            if self.record_rate != TARGET_RATE:
                num_samples = int(len(audio) * TARGET_RATE / self.record_rate)
                audio = sps.resample(audio, num_samples).astype(np.float32)
            peak = np.abs(audio).max()
            if peak > 0:
                audio = audio / peak * 0.9

            t0 = time.time()
            if self.backend == "dashscope_openai":
                text = self._transcribe_remote(audio)
            else:
                result = self.model.transcribe((audio, TARGET_RATE), language=None)
                text = ""
                if isinstance(result, list):
                    text = " ".join(r.text for r in result if hasattr(r, "text")).strip()
                elif isinstance(result, dict):
                    text = result.get("text", "").strip()
                elif isinstance(result, str):
                    text = result.strip()
            log.info("推理耗时: %.3f 秒", time.time() - t0)

            log.info("识别结果: %s", text)
            if text:
                type_text(text, target_window, target_context)
        except Exception as e:
            log.error("转写错误: %s", e)
        finally:
            self._busy = False
            _write_heartbeat({
                "recording": self.recording,
                "busy": False,
                "target_window": self.target_window,
                "target_context": self.target_context,
            })

    def _on_press(self, key):
        self.pressed.add(_normalize_key(key))
        if HOTKEY.issubset(self.pressed):
            self.start_recording()

    def _on_release(self, key):
        self.pressed.discard(_normalize_key(key))
        if self.recording and not HOTKEY.issubset(self.pressed):
            self.stop_recording()

    def run(self):
        _write_heartbeat({"status": "starting", "recording": False, "busy": False, "target_context": None})
        self.load_model()

        try:
            device, info = _pick_input_device()
            self.record_rate = int(info.get("default_samplerate") or RECORD_RATE)
            log.info("使用音频设备 %d: %s", device, info["name"])
            log.info("录音采样率: %d Hz -> %d Hz", self.record_rate, TARGET_RATE)
        except Exception as e:
            log.error("音频设备初始化失败: %s", e)
            sys.exit(1)

        self.stream = sd.InputStream(
            device=device,
            samplerate=self.record_rate,
            channels=CHANNELS,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=0,
        )
        self.stream.start()
        log.info("音频流已启动")

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        _write_heartbeat({"status": "running", "recording": False, "busy": False, "target_context": None})

        def _shutdown(sig, frame):
            self._stop_event.set()
            _write_heartbeat({"status": "stopping", "recording": self.recording, "busy": self._busy, "target_context": self.target_context})
            self.stream.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        def _toggle_record(sig, frame):
            if self._busy:
                log.info("仍在处理上一段录音，忽略本次触发")
                return
            if self.recording:
                self.stop_recording()
            else:
                self.start_recording()

        def _stop_record(sig, frame):
            if self.recording:
                self.stop_recording()

        signal.signal(signal.SIGUSR1, _toggle_record)
        signal.signal(signal.SIGUSR2, _stop_record)

        if _session_type() == "wayland":
            log.info("就绪，Wayland 模式使用系统快捷键/按钮触发录音")
            while True:
                signal.pause()
        else:
            keyboard = _keyboard_module()
            with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as listener:
                log.info("就绪，快捷键: Ctrl+Alt+Space")
                listener.join()


if __name__ == "__main__":
    VoiceInputDaemon().run()
