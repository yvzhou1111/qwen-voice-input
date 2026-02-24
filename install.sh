#!/usr/bin/env bash
# Qwen Voice Input - Linux 一键安装脚本
# 支持: Ubuntu/Debian, Arch Linux, Fedora/RHEL
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="qwen-voice-input"
DAEMON_SRC="$REPO_DIR/daemon_linux.py"
DAEMON_DST="$HOME/.local/bin/qwen-voice-input"
SERVICE_DST="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

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
            sudo apt-get install -y xdotool xclip portaudio19-dev python3-pip python3-venv
            ;;
        arch|manjaro|endeavouros)
            info "安装系统依赖 (pacman)..."
            sudo pacman -Sy --noconfirm xdotool xclip portaudio python-pip
            ;;
        fedora|rhel|centos|rocky|almalinux)
            info "安装系统依赖 (dnf)..."
            sudo dnf install -y xdotool xclip portaudio-devel python3-pip
            ;;
        opensuse*|sles)
            info "安装系统依赖 (zypper)..."
            sudo zypper install -y xdotool xclip portaudio-devel python3-pip
            ;;
        *)
            warn "未知发行版 $distro，跳过系统依赖安装，请手动安装: xdotool xclip portaudio"
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

    # 检查是否有 pyenv / venv
    if "$python" -c "import sounddevice" &>/dev/null 2>&1; then
        info "Python 依赖已安装，跳过"
        return
    fi

    info "安装 Python 依赖..."
    "$python" -m pip install --upgrade pip --quiet
    "$python" -m pip install \
        sounddevice scipy numpy pynput \
        torch --index-url https://download.pytorch.org/whl/cpu \
        transformers huggingface_hub \
        --quiet

    # 安装 qwen_asr
    if ! "$python" -c "import qwen_asr" &>/dev/null 2>&1; then
        info "安装 qwen_asr..."
        "$python" -m pip install qwen-asr --quiet || \
        "$python" -m pip install git+https://github.com/QwenLM/Qwen3-ASR.git --quiet || \
        warn "qwen_asr 安装失败，请手动安装: pip install qwen-asr"
    fi
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
After=graphical-session.target sound.target
Wants=graphical-session.target

[Service]
Type=simple
ExecStart=${python_path} ${DAEMON_DST}
Restart=on-failure
RestartSec=10
Environment=DISPLAY=:0
Environment=PYTHONUNBUFFERED=1
StandardOutput=null
StandardError=null

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user restart "$SERVICE_NAME"
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
    install_daemon "$PYTHON"
    install_service "$PYTHON"
    verify
}

main "$@"
