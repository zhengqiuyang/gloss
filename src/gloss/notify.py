"""macOS system notifications via osascript (no GUI dependencies)."""

from __future__ import annotations

import subprocess

_MAX_BODY = 180


def send(title: str, message: str) -> bool:
    """Show a notification; returns False silently when unavailable (non-mac)."""
    script = f"display notification {_q(message[:_MAX_BODY])} with title {_q(title)}"
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _q(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
