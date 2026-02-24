# Qwen Voice Input - Windows 一键安装脚本
# 需要 PowerShell 5.1+ 以管理员身份运行
#Requires -Version 5.1

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DaemonSrc = Join-Path $RepoDir "daemon_windows.py"
$InstallDir = Join-Path $env:LOCALAPPDATA "QwenVoiceInput"
$DaemonDst = Join-Path $InstallDir "daemon_windows.py"
$StartupScript = Join-Path $InstallDir "start.bat"

function Write-Info  { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

# ── 检测 Python ───────────────────────────────────────
function Get-Python {
    foreach ($py in @("python", "python3", "py")) {
        try {
            $ver = & $py --version 2>&1
            if ($ver -match "Python 3\.(1[0-9]|[2-9]\d)") {
                Write-Info "找到 Python: $ver"
                return $py
            }
        } catch {}
    }
    Write-Warn "未找到 Python 3.10+，尝试通过 winget 安装..."
    try {
        winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        return "python"
    } catch {
        Write-Err "Python 安装失败，请手动安装: https://www.python.org/downloads/"
    }
}

# ── 安装 Python 依赖 ──────────────────────────────────
function Install-PythonDeps {
    param($python)
    Write-Info "安装 Python 依赖..."

    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install sounddevice scipy numpy pynput pywin32 --quiet

    # PyTorch CPU 版本
    & $python -m pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet

    & $python -m pip install transformers huggingface_hub --quiet

    # qwen_asr
    $installed = & $python -c "import qwen_asr; print('ok')" 2>$null
    if ($installed -ne "ok") {
        Write-Info "安装 qwen_asr..."
        try {
            & $python -m pip install qwen-asr --quiet
        } catch {
            Write-Warn "qwen_asr 安装失败，请手动安装: pip install qwen-asr"
        }
    }
}

# ── 安装文件 ──────────────────────────────────────────
function Install-Files {
    param($python)
    Write-Info "安装文件到 $InstallDir ..."
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item $DaemonSrc $DaemonDst -Force

    # 生成启动脚本
    $pythonPath = (Get-Command $python).Source
    @"
@echo off
start /min "" "$pythonPath" "$DaemonDst"
"@ | Set-Content $StartupScript -Encoding UTF8

    Write-Info "文件已安装"
}

# ── 添加开机自启 ──────────────────────────────────────
function Install-Startup {
    Write-Info "配置开机自启..."
    $startupFolder = [System.Environment]::GetFolderPath("Startup")
    $shortcutPath = Join-Path $startupFolder "QwenVoiceInput.lnk"

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $StartupScript
    $shortcut.WindowStyle = 7  # 最小化
    $shortcut.Description = "Qwen Voice Input"
    $shortcut.Save()

    Write-Info "开机自启已配置: $shortcutPath"
}

# ── 启动服务 ──────────────────────────────────────────
function Start-Daemon {
    param($python)
    Write-Info "启动守护进程..."
    $pythonPath = (Get-Command $python).Source
    Start-Process -FilePath $pythonPath -ArgumentList $DaemonDst -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Write-Info "✅ 安装成功！按 Ctrl+Alt+Space 开始录音"
}

# ── 主流程 ────────────────────────────────────────────
Write-Info "=== Qwen Voice Input 安装程序 (Windows) ==="
$python = Get-Python
Install-PythonDeps $python
Install-Files $python
Install-Startup
Start-Daemon $python
