#!/usr/bin/env bash
set -eu

tty_path="$(tty 2>/dev/null || true)"
parent_comm="$(ps -o comm= -p "${PPID:-0}" 2>/dev/null | tr -d ' ' || true)"

case "$tty_path" in
  /dev/pts/*)
    case "$parent_comm" in
      gnome-terminal-|gnome-terminal-server|ptyxis-agent|ptyxis|kgx|tilix|konsole|kitty|alacritty)
        ;;
      *)
        exit 0
        ;;
    esac

    state_dir="$HOME/.local/state/qwen-voice-input"
    mkdir -p "$state_dir"
    printf '%s\n' "$tty_path" > "$state_dir/target_tty"
    if [ -n "${GNOME_TERMINAL_SCREEN:-}" ]; then
      printf '%s\n' "$GNOME_TERMINAL_SCREEN" > "$state_dir/target_screen"
    fi
    ;;
esac
