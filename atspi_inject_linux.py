#!/usr/bin/env /usr/bin/python3
import base64
import json
import sys
from typing import Any

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

TEXT_LIKE_ROLES = {
    "entry",
    "password text",
    "text",
    "terminal",
    "document text",
    "paragraph",
}
ROLE_BLACKLIST = {
    "frame",
    "panel",
    "scroll pane",
    "internal frame",
    "document web",
    "document frame",
    "desktop frame",
}


def _state_contains(acc: Any, state: Any) -> bool:
    try:
        return acc.get_state_set().contains(state)
    except Exception:
        return False


def _iter_children(acc: Any):
    try:
        count = acc.get_child_count()
    except Exception:
        return
    for idx in range(count):
        try:
            child = acc.get_child_at_index(idx)
        except Exception:
            continue
        if child is not None:
            yield child


def _find_active_apps(desktop: Any):
    apps = []
    try:
        count = desktop.get_child_count()
    except Exception:
        return apps
    for idx in range(count):
        try:
            app = desktop.get_child_at_index(idx)
        except Exception:
            continue
        if app is None:
            continue
        if _state_contains(app, Atspi.StateType.ACTIVE):
            apps.append(app)
    return apps


def _find_focused(root: Any):
    stack = [root]
    seen = 0
    while stack:
        acc = stack.pop()
        seen += 1
        if seen > 20000:
            break
        if _state_contains(acc, Atspi.StateType.FOCUSED):
            return acc
        for child in _iter_children(acc):
            stack.append(child)
    return None


def _path_to_root(acc: Any):
    path = []
    cur = acc
    while cur is not None:
        path.append(cur)
        try:
            cur = cur.get_parent()
        except Exception:
            break
    return list(reversed(path))


def _safe_role(acc: Any) -> str:
    try:
        return (acc.get_role_name() or "").lower()
    except Exception:
        return ""


def _safe_name(acc: Any) -> str:
    try:
        return acc.get_name() or ""
    except Exception:
        return ""


def _extract_context(path):
    app_name = ""
    pid = None
    window_title = ""
    roles = []
    editable = False
    has_text_iface = False

    for node in path:
        role = _safe_role(node)
        if role:
            roles.append(role)

        if role == "application" and not app_name:
            app_name = _safe_name(node).lower()
            try:
                pid = int(node.get_process_id())
            except Exception:
                pid = None

        if not window_title and role in {"frame", "window", "dialog"}:
            window_title = _safe_name(node)

        if not editable:
            try:
                editable = bool(node.get_editable_text_iface())
            except Exception:
                editable = False

        if not has_text_iface:
            try:
                has_text_iface = bool(node.get_text_iface())
            except Exception:
                has_text_iface = False

    focus_role = roles[-1] if roles else ""
    focus_textish = focus_role in TEXT_LIKE_ROLES
    focus_editable = False
    try:
        focus = path[-1]
        focus_editable = _state_contains(focus, Atspi.StateType.EDITABLE)
    except Exception:
        focus_editable = False
    terminal_like = (
        ("terminal" in app_name)
        or ("gnome-terminal" in app_name)
        or ("ptyxis" in app_name)
        or ("terminal" in roles)
    )

    return {
        "ok": True,
        "app_name": app_name,
        "pid": pid,
        "window_title": window_title,
        "roles": roles,
        "focus_role": focus_role,
        "textish": bool(focus_textish),
        "focus_editable": bool(focus_editable),
        "terminal_like": bool(terminal_like),
        "editable": bool(editable),
        "has_text_iface": bool(has_text_iface),
    }


def focus_info() -> int:
    Atspi.init()
    desktop = Atspi.get_desktop(0)
    focused = None

    for app in _find_active_apps(desktop):
        focused = _find_focused(app)
        if focused is not None:
            break

    if focused is None:
        focused = _find_focused(desktop)

    if focused is None:
        print(json.dumps({"ok": False, "error": "focus_not_found"}, ensure_ascii=False))
        return 1

    path = _path_to_root(focused)
    print(json.dumps(_extract_context(path), ensure_ascii=False))
    return 0


def _find_editable_node(focused, path):
    for node in reversed(path):
        role = _safe_role(node)
        if role in ROLE_BLACKLIST:
            continue
        try:
            editable_iface = node.get_editable_text_iface()
        except Exception:
            editable_iface = None
        if editable_iface and (_state_contains(node, Atspi.StateType.EDITABLE) or role in TEXT_LIKE_ROLES):
            return node, editable_iface

    stack = [focused]
    seen = 0
    while stack:
        node = stack.pop()
        seen += 1
        if seen > 5000:
            break
        role = _safe_role(node)
        if role in ROLE_BLACKLIST:
            for child in _iter_children(node):
                stack.append(child)
            continue
        try:
            editable_iface = node.get_editable_text_iface()
        except Exception:
            editable_iface = None
        if editable_iface and (_state_contains(node, Atspi.StateType.EDITABLE) or role in TEXT_LIKE_ROLES):
            return node, editable_iface
        for child in _iter_children(node):
            stack.append(child)
    return None, None


def insert_text(payload_b64: str) -> int:
    try:
        text = base64.b64decode(payload_b64.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        print("invalid base64 payload", file=sys.stderr)
        return 2

    safe_text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if not safe_text.strip():
        return 0

    Atspi.init()
    desktop = Atspi.get_desktop(0)
    focused = None

    for app in _find_active_apps(desktop):
        focused = _find_focused(app)
        if focused is not None:
            break

    if focused is None:
        focused = _find_focused(desktop)

    if focused is None:
        print("focus not found", file=sys.stderr)
        return 1

    path = _path_to_root(focused)
    node, editable_iface = _find_editable_node(focused, path)
    if node is None or editable_iface is None:
        print("no editable target", file=sys.stderr)
        return 3

    caret = 0
    try:
        text_iface = node.get_text_iface()
    except Exception:
        text_iface = None

    if text_iface is not None:
        try:
            selections = text_iface.get_text_selections() or []
            if selections:
                sel = selections[0]
                start = int(sel.start_offset)
                end = int(sel.end_offset)
                if start != end:
                    lo, hi = (start, end) if start < end else (end, start)
                    try:
                        editable_iface.delete_text(lo, hi)
                    except Exception:
                        pass
                    caret = lo
                else:
                    caret = int(text_iface.get_caret_offset())
            else:
                caret = int(text_iface.get_caret_offset())
        except Exception:
            caret = 0

    try:
        editable_iface.insert_text(caret, safe_text, len(safe_text))
    except Exception as exc:
        print(f"insert failed: {exc}", file=sys.stderr)
        return 4
    return 0


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: atspi_inject_linux.py <focus-info|insert> [payload-base64]", file=sys.stderr)
        return 2
    cmd = argv[1].strip()
    if cmd == "focus-info":
        return focus_info()
    if cmd == "insert":
        if len(argv) != 3:
            print("usage: atspi_inject_linux.py insert <payload-base64>", file=sys.stderr)
            return 2
        return insert_text(argv[2])
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
