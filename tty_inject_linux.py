#!/usr/bin/python3
import base64
import fcntl
import os
import sys
import termios
import time


def main():
    if len(sys.argv) not in {3, 4}:
        print("usage: qwen-voice-input-tty-inject <tty> <payload-base64> [delay-ms]", file=sys.stderr)
        return 2

    tty_path = sys.argv[1]
    payload = base64.b64decode(sys.argv[2])
    delay_ms = 0.0
    if len(sys.argv) == 4:
        try:
            delay_ms = max(0.0, float(sys.argv[3]))
        except ValueError:
            print("delay-ms must be numeric", file=sys.stderr)
            return 2

    fd = os.open(tty_path, os.O_RDWR | os.O_NOCTTY)
    try:
        for b in payload:
            fcntl.ioctl(fd, termios.TIOCSTI, bytes([b]))
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

