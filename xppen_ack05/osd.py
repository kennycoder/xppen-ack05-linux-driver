"""Best-effort non-blocking KDE Plasma OSD helper."""

from __future__ import annotations

import shutil
import subprocess


def show_osd(text: str, icon: str = "input-dialpad") -> None:
    """Display a transient KDE Plasma 6 OSD overlay if qdbus6 is available."""
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if not qdbus:
        return
    try:
        subprocess.Popen(
            [
                qdbus,
                "org.kde.plasmashell",
                "/org/kde/osdService",
                "org.kde.osdService.showText",
                icon,
                text,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass
