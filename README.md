# Qwen Voice Input

基于 DashScope/OpenAI-compatible ASR API 的语音输入工具。按住 `Ctrl+Alt+Space` 录音，松开后自动将识别结果输入到当前焦点窗口。

- 使用远程 API 识别语音
- 无弹窗，无痕输入
- 不使用剪贴板注入（不会污染剪贴板）
- 支持 Linux（GNOME Wayland / X11）和 Windows
- 当前实现使用 OpenAI-compatible / DashScope 后端

## 安装

### Linux（Ubuntu / Debian / Arch / Fedora）

```bash
git clone https://github.com/yvzhou1111/qwen-voice-input.git
cd qwen-voice-input
chmod +x install.sh
./install.sh
```

### Windows

以管理员身份打开 PowerShell，运行：

```powershell
git clone https://github.com/yvzhou1111/qwen-voice-input.git
cd qwen-voice-input
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

## 使用

| 操作 | 说明 |
|------|------|
| 按住 `Ctrl+Alt+Space` | 开始录音 |
| 松开 | 停止录音，自动识别并输入 |

首次使用前需要在 `~/.config/qwen-voice-input.env` 中配置 `DASHSCOPE_API_KEY`。

## 系统要求

| 平台 | 要求 |
|------|------|
| Linux | GNOME Wayland 或 X11、xdotool、python3-gi(at-spi2)、Python 3.10+ |
| Windows | Windows 10/11、Python 3.10+ |
| 通用 | 麦克风、4GB+ RAM |

## 卸载

### Linux
```bash
systemctl --user stop qwen-voice-input
systemctl --user disable qwen-voice-input
rm ~/.local/bin/qwen-voice-input
rm ~/.config/systemd/user/qwen-voice-input.service
```

### Windows
删除 `%LOCALAPPDATA%\QwenVoiceInput` 目录，并删除启动文件夹中的快捷方式。

## License

MIT
