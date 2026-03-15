#!/usr/bin/env bash
# Qwen Voice Input - Linux 一键安装脚本
# 支持: Ubuntu/Debian, Arch Linux, Fedora/RHEL
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="qwen-voice-input"
DAEMON_SRC="$REPO_DIR/daemon_linux.py"
DAEMON_DST="$HOME/.local/bin/qwen-voice-input"
HEALTHCHECK_SRC="$REPO_DIR/healthcheck_linux.py"
HEALTHCHECK_DST="$HOME/.local/bin/qwen-voice-input-healthcheck"
CLIPBOARD_HISTORY_SRC="$REPO_DIR/clipboard_history_linux.py"
CLIPBOARD_HISTORY_DST="$HOME/.local/bin/qwen-clipboard-history"
SESSION_FIXES_SRC="$REPO_DIR/session_fixes.py"
SESSION_FIXES_DST="$HOME/.local/bin/qwen-session-fixes"
SESSION_FIXES_SERVICE_DST="$HOME/.config/systemd/user/qwen-session-fixes.service"
ATSPI_HELPER_SRC="$REPO_DIR/atspi_inject_linux.py"
ATSPI_HELPER_DST="$HOME/.local/bin/qwen-voice-input-atspi"
HOTKEY_HELPER_SRC="$REPO_DIR/hotkey_helper_linux.py"
HOTKEY_HELPER_DST="/usr/local/bin/qwen-voice-input-hotkey-helper"
HOTKEY_SERVICE_SRC="$REPO_DIR/systemd/qwen-voice-input-hotkey@.service"
HOTKEY_SERVICE_DST="/etc/systemd/system/qwen-voice-input-hotkey@.service"
SERVICE_DST="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
HEALTH_SERVICE_DST="$HOME/.config/systemd/user/${SERVICE_NAME}-health.service"
HEALTH_TIMER_DST="$HOME/.config/systemd/user/${SERVICE_NAME}-health.timer"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 检测发行版 ────────────────────────────────────────
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    else
        error "无法检测发行版"
    fi
}

# ── 安装系统依赖 ──────────────────────────────────────
install_deps() {
    local distro
    distro=$(detect_distro)
    info "发行版: $distro"

    case "$distro" in
        ubuntu|debian|linuxmint|pop)
            info "安装系统依赖 (apt)..."
            sudo apt-get update -qq
            sudo apt-get install -y \
                xdotool xclip x11-utils \
                portaudio19-dev python3-pip python3-venv \
                python3-gi gir1.2-atspi-2.0 at-spi2-core \
                python3-evdev
            ;;
        arch|manjaro|endeavouros)
            info "安装系统依赖 (pacman)..."
            sudo pacman -Sy --noconfirm xdotool xclip xorg-xprop portaudio python-pip
            ;;
        fedora|rhel|centos|rocky|almalinux)
            info "安装系统依赖 (dnf)..."
            sudo dnf install -y xdotool xclip xprop portaudio-devel python3-pip
            ;;
        opensuse*|sles)
            info "安装系统依赖 (zypper)..."
            sudo zypper install -y xdotool xclip xprop portaudio-devel python3-pip
            ;;
        *)
            warn "未知发行版 $distro，跳过系统依赖安装，请手动安装: xdotool xclip xprop portaudio"
            ;;
    esac
}

# ── 检测 Python ───────────────────────────────────────
detect_python() {
    for py in python3.12 python3.11 python3.10 python3; do
        if command -v "$py" &>/dev/null; then
            echo "$py"
            return
        fi
    done
    error "未找到 Python 3.10+，请先安装"
}

# ── 安装 Python 依赖 ──────────────────────────────────
install_python_deps() {
    local python="$1"
    info "使用 Python: $($python --version)"

    if "$python" - <<'PY' &>/dev/null 2>&1
import importlib.util, sys
required = [
    'sounddevice', 'scipy', 'numpy', 'pynput',
    'torch', 'transformers', 'huggingface_hub', 'qwen_asr',
    'openai'
]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(0 if not missing else 1)
PY
    then
        info "Python 依赖已安装，跳过"
        return
    fi

    info "安装 Python 依赖..."
    "$python" -m pip install --upgrade pip --quiet
    "$python" -m pip install \
        sounddevice scipy numpy pynput transformers huggingface_hub requests openai \
        --quiet

    if ! "$python" -c "import torch" &>/dev/null 2>&1; then
        info "安装 torch..."
        "$python" -m pip install torch --quiet
    fi

    # 安装 qwen_asr
    if ! "$python" -c "import qwen_asr" &>/dev/null 2>&1; then
        info "安装 qwen_asr..."
        "$python" -m pip install qwen-asr --quiet || \
        "$python" -m pip install git+https://github.com/QwenLM/Qwen3-ASR.git --quiet || \
        warn "qwen_asr 安装失败，请手动安装: pip install qwen-asr"
    fi
}

# ── 预下载模型到本地缓存 ──────────────────────────────
prefetch_model() {
    local python="$1"
    if [ "${QWEN_VOICE_BACKEND:-local}" != "local" ]; then
        info "QWEN_VOICE_BACKEND!=local，跳过模型预下载"
        return
    fi
    info "检查本地模型缓存..."
    "$python" - <<'PY'
from huggingface_hub import snapshot_download

path = snapshot_download("Qwen/Qwen3-ASR-0.6B")
print(f"MODEL_CACHE={path}")
PY
}

# ── 安装 daemon ───────────────────────────────────────
install_daemon() {
    local python="$1"
    info "安装 daemon..."
    mkdir -p "$HOME/.local/bin"
    cp "$DAEMON_SRC" "$DAEMON_DST"
    # 替换 shebang 为实际 python 路径
    local python_path
    python_path=$(command -v "$python")
    sed -i "1s|.*|#!${python_path}|" "$DAEMON_DST"
    chmod +x "$DAEMON_DST"
    info "daemon 已安装到 $DAEMON_DST"
}

install_atspi_helper() {
    info "安装 AT-SPI 注入器 (不使用剪贴板)..."
    mkdir -p "$HOME/.local/bin"
    cp "$ATSPI_HELPER_SRC" "$ATSPI_HELPER_DST"
    chmod +x "$ATSPI_HELPER_DST"
    info "AT-SPI helper 已安装到 $ATSPI_HELPER_DST"
}

install_hotkey_service() {
    info "安装全局热键监听 (systemd system service)..."
    sudo install -m 0755 "$HOTKEY_HELPER_SRC" "$HOTKEY_HELPER_DST"
    sudo install -m 0644 "$HOTKEY_SERVICE_SRC" "$HOTKEY_SERVICE_DST"
    sudo systemctl daemon-reload
    sudo systemctl enable --now "qwen-voice-input-hotkey@$(whoami).service"
}

install_healthcheck() {
    local python="$1"
    info "安装 healthcheck..."
    mkdir -p "$HOME/.local/bin"
    cp "$HEALTHCHECK_SRC" "$HEALTHCHECK_DST"
    local python_path
    python_path=$(command -v "$python")
    sed -i "1s|.*|#!${python_path}|" "$HEALTHCHECK_DST"
    chmod +x "$HEALTHCHECK_DST"
    info "healthcheck 已安装到 $HEALTHCHECK_DST"
}


install_clipboard_history() {
    local python="$1"
    info "安装 clipboard history browser..."
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
    cp "$CLIPBOARD_HISTORY_SRC" "$CLIPBOARD_HISTORY_DST"
    local history_python="/usr/bin/python3"
    if ! "$history_python" -c "import gi" &>/dev/null 2>&1; then
        history_python=$(command -v "$python")
    fi
    sed -i "1s|.*|#!${history_python}|" "$CLIPBOARD_HISTORY_DST"
    chmod +x "$CLIPBOARD_HISTORY_DST"

    cat > "$HOME/.local/share/applications/qwen-clipboard-history.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Clipboard Full History
Comment=Browse full clipboard history in a scrollable window
Exec=${CLIPBOARD_HISTORY_DST}
Terminal=false
Categories=Utility;
EOF
}


install_session_fixes() {
    local python="$1"
    info "安装 session fixes..."
    mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
    cp "$SESSION_FIXES_SRC" "$SESSION_FIXES_DST"
    local python_path
    python_path=$(command -v "$python")
    sed -i "1s|.*|#!${python_path}|" "$SESSION_FIXES_DST"
    chmod +x "$SESSION_FIXES_DST"

    cat > "$SESSION_FIXES_SERVICE_DST" <<EOF
[Unit]
Description=Qwen 桌面输入环境修复
After=graphical-session.target
Wants=graphical-session.target

[Service]
Type=oneshot
ExecStart=${python_path} ${SESSION_FIXES_DST}
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus

[Install]
WantedBy=default.target
EOF
}

# ── 自动检测音频设备 ──────────────────────────────────
detect_audio_device() {
    local python="$1"
    local device_id
    device_id=$("$python" - <<'EOF'
import sounddevice as sd, sys
try:
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            print(i)
            sys.exit(0)
    print("None")
except Exception:
    print("None")
EOF
)
    echo "$device_id"
}

# ── 安装 systemd 服务 ─────────────────────────────────
install_service() {
    local python="$1"
    local python_path
    python_path=$(command -v "$python")

    info "配置 systemd 服务..."
    mkdir -p "$HOME/.config/systemd/user"

    cat > "$SERVICE_DST" <<EOF
[Unit]
Description=Qwen3-ASR 语音输入守护进程
After=graphical-session.target sound.target pipewire.service pipewire-pulse.service
Wants=graphical-session.target
StartLimitIntervalSec=300
StartLimitBurst=2

[Service]
Type=simple
ExecStart=${python_path} ${DAEMON_DST}
Restart=on-failure
RestartSec=30
Environment=DISPLAY=:0
Environment=PYTHONUNBUFFERED=1
Environment=QWEN_VOICE_GUI_INPUT_MODE=auto
EnvironmentFile=-%h/.config/qwen-voice-input.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

    cat > "$HEALTH_SERVICE_DST" <<EOF
[Unit]
Description=Qwen Voice Input 健康检查
After=default.target

[Service]
Type=oneshot
ExecStart=${python_path} ${HEALTHCHECK_DST}
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
EOF

    cat > "$HEALTH_TIMER_DST" <<EOF
[Unit]
Description=定时检查 Qwen Voice Input 状态

[Timer]
OnBootSec=45s
OnUnitActiveSec=60s
Persistent=true
Unit=${SERVICE_NAME}-health.service

[Install]
WantedBy=timers.target
EOF

    systemctl --user import-environment DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR XDG_SESSION_TYPE WAYLAND_DISPLAY || true
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user enable "${SERVICE_NAME}-health.timer"
    systemctl --user enable "qwen-session-fixes.service" || true
    systemctl --user restart "$SERVICE_NAME"
    systemctl --user restart "${SERVICE_NAME}-health.timer"
    systemctl --user restart "qwen-session-fixes.service" || true
    info "服务已启动"
}

# ── 验证安装 ──────────────────────────────────────────
verify() {
    sleep 3
    if systemctl --user is-active --quiet "$SERVICE_NAME"; then
        info "✅ 安装成功！按 Ctrl+Alt+Space 开始录音"
    else
        warn "服务未正常启动，查看日志: journalctl --user -u $SERVICE_NAME -n 20"
    fi
}

# ── 主流程 ────────────────────────────────────────────
main() {
    info "=== Qwen Voice Input 安装程序 ==="
    install_deps
    PYTHON=$(detect_python)
    install_python_deps "$PYTHON"
    prefetch_model "$PYTHON"
    install_daemon "$PYTHON"
    install_atspi_helper
    install_healthcheck "$PYTHON"
    install_clipboard_history "$PYTHON"
    install_session_fixes "$PYTHON"
    install_hotkey_service
    install_service "$PYTHON"
    verify
}

main "$@"
