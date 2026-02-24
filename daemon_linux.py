#!/usr/bin/env python3
"""
Qwen3-ASR 语音输入守护进程 - Linux
快捷键: Ctrl+Alt+Space 按住录音，松开转文字并输入
"""

import os
import sys
import time
import threading
import subprocess
import logging
import signal
import numpy as np
import sounddevice as sd
import scipy.signal as sps
from pynput import keyboard

# ── 配置 ──────────────────────────────────────────────
MODEL_NAME  = "Qwen/Qwen3-ASR-0.6B"
RECORD_RATE = 44100
TARGET_RATE = 16000
CHANNELS    = 1
HOTKEY      = {keyboard.Key.ctrl_l, keyboard.Key.alt_l, keyboard.Key.space}
# ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def _env():
    return {**os.environ,
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")}


def _diodon_pause(env):
    try:
        subprocess.run(
            ["gsettings", "set", "net.launchpad.Diodon.clipboard", "use-clipboard", "false"],
            env=env, timeout=3
        )
    except Exception:
        pass


def _diodon_resume(env):
    try:
        subprocess.run(
            ["gsettings", "set", "net.launchpad.Diodon.clipboard", "use-clipboard", "true"],
            env=env, timeout=3
        )
    except Exception:
        pass


def type_text(text, target_window=None):
    env = _env()
    try:
        if target_window:
            win_name = subprocess.run(
                ["xdotool", "getwindowname", str(target_window)],
                capture_output=True, text=True, env=env, timeout=3
            ).stdout.strip().lower()
            is_terminal = any(t in win_name for t in
                              ["terminal", "konsole", "xterm", "bash", "zsh", "fish", "@", "终端"])
            if not is_terminal:
                term_ids = subprocess.run(
                    ["xdotool", "search", "--class", "gnome-terminal"],
                    capture_output=True, text=True, env=env, timeout=3
                ).stdout.strip().split()
                for tid in term_ids:
                    tname = subprocess.run(
                        ["xdotool", "getwindowname", tid],
                        capture_output=True, text=True, env=env, timeout=3
                    ).stdout.strip().lower()
                    if any(t in tname for t in ["终端", "terminal", "@", "bash", "zsh"]):
                        target_window = tid
                        win_name = tname
                        is_terminal = True
                        break
        else:
            win_name = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, env=env, timeout=3
            ).stdout.strip().lower()
            is_terminal = any(t in win_name for t in
                              ["terminal", "konsole", "xterm", "bash", "zsh", "fish", "@", "终端"])

        paste_key = "ctrl+shift+v" if is_terminal else "ctrl+v"

        _diodon_pause(env)
        time.sleep(0.05)

        backup = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True, env=env, timeout=3
        ).stdout
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-i"],
            input=text.encode("utf-8"), env=env, timeout=3
        )
        time.sleep(0.05)

        if target_window:
            try:
                subprocess.run(
                    ["xdotool", "windowfocus", "--sync", str(target_window)],
                    env=env, timeout=3, check=True
                )
                time.sleep(0.05)
            except Exception:
                pass

        subprocess.run(["xdotool", "key", "--clearmodifiers", paste_key], env=env, timeout=5)
        time.sleep(0.1)

        subprocess.run(
            ["xclip", "-selection", "clipboard", "-i"],
            input=backup, env=env, timeout=3
        )
        _diodon_resume(env)
    except Exception as e:
        log.error("type_text error: %s", e)
        _diodon_resume(env)


class VoiceInputDaemon:
    def __init__(self):
        self.model         = None
        self.recording     = False
        self.audio_buf     = []
        self.pressed       = set()
        self.lock          = threading.Lock()
        self.stream        = None
        self._busy         = False
        self.target_window = None

    def load_model(self):
        log.info("正在加载模型 %s ...", MODEL_NAME)
        try:
            import torch
            from qwen_asr import Qwen3ASRModel

            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            dtype  = torch.float16 if torch.cuda.is_available() else torch.float32
            log.info("设备: %s  dtype: %s", device, dtype)

            self.model = Qwen3ASRModel.from_pretrained(
                MODEL_NAME, dtype=dtype, device_map=device, max_new_tokens=128
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

    def _audio_callback(self, indata, frames, time_info, status):
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
                self.target_window = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True, text=True, env=env, timeout=2
                ).stdout.strip()
            except Exception:
                self.target_window = None
        log.info("开始录音，目标窗口: %s", self.target_window)

    def stop_recording(self):
        with self.lock:
            if not self.recording:
                return
            self.recording = False
            buf = list(self.audio_buf)
        threading.Thread(target=self._transcribe, args=(buf, self.target_window), daemon=True).start()

    def _transcribe(self, buf, target_window=None):
        self._busy = True
        try:
            if len(buf) < 5:
                return
            audio = np.concatenate(buf, axis=0).flatten().astype(np.float32)
            num_samples = int(len(audio) * TARGET_RATE / RECORD_RATE)
            audio = sps.resample(audio, num_samples).astype(np.float32)
            peak = np.abs(audio).max()
            if peak > 0:
                audio = audio / peak * 0.9

            t0 = time.time()
            result = self.model.transcribe((audio, TARGET_RATE), language=None)
            log.info("推理耗时: %.3f 秒", time.time() - t0)

            text = ""
            if isinstance(result, list):
                text = " ".join(r.text for r in result if hasattr(r, "text")).strip()
            elif isinstance(result, dict):
                text = result.get("text", "").strip()
            elif isinstance(result, str):
                text = result.strip()

            log.info("识别结果: %s", text)
            if text:
                type_text(text, target_window)
        except Exception as e:
            log.error("转写错误: %s", e)
        finally:
            self._busy = False

    def _on_press(self, key):
        self.pressed.add(key)
        if HOTKEY.issubset(self.pressed):
            self.start_recording()

    def _on_release(self, key):
        self.pressed.discard(key)
        if self.recording and not HOTKEY.issubset(self.pressed):
            self.stop_recording()

    def run(self):
        self.load_model()

        # 自动检测音频设备
        device = None
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    device = i
                    log.info("使用音频设备 %d: %s", i, d["name"])
                    break
        except Exception:
            pass

        self.stream = sd.InputStream(
            device=device,
            samplerate=RECORD_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=1024,
        )
        self.stream.start()
        log.info("音频流已启动")

        def _shutdown(sig, frame):
            self.stream.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as listener:
            log.info("就绪，快捷键: Ctrl+Alt+Space")
            listener.join()


if __name__ == "__main__":
    VoiceInputDaemon().run()
