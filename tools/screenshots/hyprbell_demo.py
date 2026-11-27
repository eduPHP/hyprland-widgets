#!/usr/bin/env python3
"""Made-up notification history for the hyprbell README screenshots.

    tools/screenshots/hyprbell_demo.py STATE_DIR   writes STATE_DIR/hyprbell/history.jsonl and seen

Run a demo copy of hyprbell on it, in the default Tokyo Night tokens and without logging real
notifications:
    XDG_STATE_HOME=STATE_DIR HYPRBELL_RUNTIME_DIR=STATE_DIR HYPRBELL_NO_MONITOR=1 hyprbell --daemon
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

now = datetime.now().replace(second=0, microsecond=0)
midnight = now.replace(hour=0, minute=0)


def at(minutes_ago: int) -> float:
    return (now - timedelta(minutes=minutes_ago)).timestamp()


ENTRIES = [  # oldest first, like the real file
    (60 * 24 + 600 + (now - midnight).seconds // 60, "Software Updater", "update-manager",
     "Updates are available", "14 packages can be upgraded, 3 of them security updates.", 1, {}),
    (305 + (now - midnight).seconds // 60, "hyprpower", "", "Battery at 20%",
     "About 1h 05m left on balanced. Plug in or switch to power-saver.", 1, {}),
    (190 + (now - midnight).seconds // 60, "Claude", "com.anthropic.Claude", "Lock screen installed",
     "hyprlock now matches the login screen. Your old hyprlock.conf is backed up.", 0, {}),
    (420, "udiskie", "", "Device mounted", "SANDISK 32GB mounted on /media/demo/SANDISK", 0, {}),
    (250, "WhatsApp", "", "Ana", "are we still on for the market on saturday?", 1, {"dnd": True}),
    (208, "disk-alert", "", "Low disk space on /boot/efi",
     "1G free (4% used). Old kernels and UKIs pile up here; remove unused ones.", 2, {}),
    (195, "notify-send", "", "Screenshot saved & copied", "~/Pictures/screenshots/2026-11-25_20-41-07.png", 1, {}),
    (131, "disk-alert", "", "Low disk space on /boot/efi",
     "2G free (4% used). A full disk silently breaks /boot/limine.conf; clean up snapper, docker and caches.", 2, {}),
    (96, "hyprcal", "", "Climbing gym in 10 min", "22:30 – 23:30 · personal", 1, {}),
    (2, "Claude", "com.anthropic.Claude", "Review ready: notification history",
     "The design for hyprbell is up. Open the thread to review it.", 0, {}),
]


def main():
    state = Path(sys.argv[1]) / "hyprbell"
    state.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (ago, app, entry, summary, body, urg, extra) in enumerate(ENTRIES):
        ts = at(ago)
        e = {"ts": ts, "id": 0, "app": app, "icon": "", "summary": summary, "body": body, "actions": [],
             "urgency": urg, "entry": entry, "key": f"demo{i}", "closed": True, **extra}
        lines.append(json.dumps(e, ensure_ascii=False))
    (state / "history.jsonl").write_text("\n".join(lines) + "\n")
    (state / "seen").write_text(f"{at(140):.3f}")  # the last three are new
    print(state)


if __name__ == "__main__":
    main()
