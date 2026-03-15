#!/usr/bin/env python3
import subprocess
import time
from pathlib import Path

FCITX_PROFILE = Path.home() / '.config/fcitx5/profile'

PROFILE_CONTENT = """[Groups/0]
# Group Name
Name=默认
# Layout
Default Layout=us
# Default Input Method
DefaultIM=pinyin

[Groups/0/Items/0]
# Name
Name=keyboard-us
# Layout
Layout=

[Groups/0/Items/1]
# Name
Name=pinyin
# Layout
Layout=

[GroupOrder]
0=默认
"""


def run(cmd):
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_capture(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def write_fcitx_profile():
    FCITX_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    FCITX_PROFILE.write_text(PROFILE_CONTENT)


def set_fcitx_active_by_default():
    config_path = Path.home() / '.config/fcitx5/config'
    if not config_path.exists():
        return
    text = config_path.read_text()
    target = '# Active By Default\nActiveByDefault=False'
    replacement = '# Active By Default\nActiveByDefault=True'
    if target in text:
        text = text.replace(target, replacement)
    elif 'ActiveByDefault=True' not in text:
        text += '\n[Behavior]\n# Active By Default\nActiveByDefault=True\n'
    config_path.write_text(text)


def ensure_fcitx_runtime_group():
    current = run_capture([
        'gdbus', 'call', '--session', '--dest', 'org.fcitx.Fcitx5',
        '--object-path', '/controller',
        '--method', 'org.fcitx.Fcitx.Controller1.InputMethodGroupInfo', '默认'
    ])
    if 'pinyin' not in current.stdout:
        run([
            'gdbus', 'call', '--session', '--dest', 'org.fcitx.Fcitx5',
            '--object-path', '/controller',
            '--method', 'org.fcitx.Fcitx.Controller1.SetInputMethodGroupInfo',
            '默认', 'us', '[("keyboard-us", ""), ("pinyin", "")]'
        ])
        run([
            'gdbus', 'call', '--session', '--dest', 'org.fcitx.Fcitx5',
            '--object-path', '/controller',
            '--method', 'org.fcitx.Fcitx.Controller1.Save'
        ])


def set_current_im_to_pinyin():
    run(['fcitx5-remote', '-o'])
    run([
        'gdbus', 'call', '--session', '--dest', 'org.fcitx.Fcitx5',
        '--object-path', '/controller',
        '--method', 'org.fcitx.Fcitx.Controller1.SetCurrentIM', 'pinyin'
    ])


def ensure_custom_keybinding(name, command, binding, key_id="custom6"):
    base = "org.gnome.settings-daemon.plugins.media-keys"
    key_path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{key_id}/"
    try:
        existing = subprocess.check_output([
            "gsettings", "get", base, "custom-keybindings"
        ], text=True).strip()
    except Exception:
        existing = "[]"

    paths = [part.strip().strip("'") for part in existing.strip('[]').split(',') if part.strip()]
    if key_path not in paths:
        paths.append(key_path)
        formatted = '[' + ', '.join(f"'{item}'" for item in paths) + ']'
        run(["gsettings", "set", base, "custom-keybindings", formatted])

    schema = f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{key_path}"
    run(["gsettings", "set", schema, "name", name])
    run(["gsettings", "set", schema, "command", command])
    run(["gsettings", "set", schema, "binding", binding])


def main():
    write_fcitx_profile()
    set_fcitx_active_by_default()

    run(["gsettings", "set", "net.launchpad.Diodon.clipboard", "use-primary", "false"])
    run(["gsettings", "set", "net.launchpad.Diodon.clipboard", "synchronize-clipboards", "false"])
    run(["gsettings", "set", "net.launchpad.Diodon.clipboard", "recent-items-size", "200"])
    run(["gsettings", "set", "net.launchpad.Diodon.clipboard", "instant-paste", "false"])
    run(["gsettings", "set", "org.gnome.desktop.input-sources", "sources", "[('xkb', 'us')]"])
    run(["gsettings", "set", "org.gnome.desktop.input-sources", "mru-sources", "[('xkb', 'us')]"])
    run(["im-config", "-n", "fcitx5"])
    run(["setxkbmap", "us"])
    run(["fcitx5-remote", "-r"])
    ensure_fcitx_runtime_group()
    set_current_im_to_pinyin()
    ensure_custom_keybinding(
        "Clipboard Full History",
        "/home/yilis/.local/bin/qwen-clipboard-history",
        "<Ctrl><Alt>v",
    )
    run(["pkill", "-x", "diodon"])
    time.sleep(1)
    subprocess.Popen(["/usr/bin/diodon"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
