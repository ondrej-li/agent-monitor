"""Native macOS notifications (Notification Center) via osascript."""

from __future__ import annotations

import subprocess
import sys


def _escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')


def macos(title: str, message: str, subtitle: str = "", sound: bool = True) -> tuple[bool, str]:
    """Show a Notification Center banner. Returns (sent, detail)."""
    if sys.platform != "darwin":
        return False, "not macOS"
    parts = [
        f'display notification "{_escape(message)}"',
        f'with title "{_escape(title)}"',
    ]
    if subtitle:
        parts.append(f'subtitle "{_escape(subtitle[:180])}"')
    if sound:
        parts.append('sound name "Ping"')
    try:
        proc = subprocess.run(
            ["osascript", "-e", " ".join(parts)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stderr or "").strip()


def summarize(briefing: dict) -> tuple[str, int]:
    """Return a one-line summary and the number of items that need attention."""
    needs = len(briefing.get("needs_you") or [])
    risks = len(briefing.get("risks") or [])
    ideas = len(briefing.get("ideas") or [])
    bits = [f"{needs} need you"]
    if risks:
        bits.append(f"{risks} risk{'s' if risks > 1 else ''}")
    if ideas:
        bits.append(f"{ideas} idea{'s' if ideas > 1 else ''}")
    return " · ".join(bits), needs + risks


def briefing_worth_attention(cfg, briefing: dict) -> tuple[bool, str]:
    enabled = bool(cfg.get("notifications.macos", True))
    summary, weight = summarize(briefing)
    minimum = int(cfg.get("notifications.min_alerts", 1))
    return enabled and weight >= minimum, summary
