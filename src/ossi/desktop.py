"""Adds OSSI to the Desktop, with its own icon.

A shortcut made by hand points at OSSI.pyw, and so shows Python's icon instead of OSSI's.
"""

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from ossi import autostart, storage

LINK_NAME = "OSSI.lnk"
ICON = Path(__file__).parent / "assets" / "ossi.ico"
DESKTOP_FOLDER = 0x10  # Windows' number for this, which follows the Desktop if it was moved.
FOLDER_LENGTH = 260
SAVE_TIMEOUT_SECONDS = 30
# The details travel in the environment, so a folder's name can't be run as PowerShell code.
SAVE_SHORTCUT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:OSSI_LINK);"
    "$s.TargetPath = $env:OSSI_TARGET;"
    "$s.Arguments = $env:OSSI_ARGUMENTS;"
    "$s.WorkingDirectory = $env:OSSI_FOLDER;"
    "$s.IconLocation = $env:OSSI_ICON;"
    "$s.Description = 'OSSI personal assistant';"
    "$s.Save()"
)


def supported():
    return sys.platform == "win32"


def link_path():
    buffer = ctypes.create_unicode_buffer(FOLDER_LENGTH)
    ctypes.windll.shell32.SHGetFolderPathW(None, DESKTOP_FOLDER, None, 0, buffer)
    return Path(buffer.value) / LINK_NAME


def exists():
    return link_path().is_file()


def create():
    """Add the shortcut, or correct it if OSSI has moved. Returns True, or raises if it can't."""
    python, arguments = autostart.launch_parts()
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", SAVE_SHORTCUT],
        check=True,
        capture_output=True,
        timeout=SAVE_TIMEOUT_SECONDS,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env={
            **os.environ,
            "OSSI_LINK": str(link_path()),
            "OSSI_TARGET": str(python),
            "OSSI_ARGUMENTS": arguments,
            "OSSI_FOLDER": str(autostart.LAUNCHER.parent),
            "OSSI_ICON": f"{ICON},0",
        },
    )
    return True


def should_offer(db):
    """Whether to ask about the shortcut. It's only ever asked once."""
    if storage.get_setting(db, "desktop_offer_made") or not supported():
        return False
    if exists():
        mark_offered(db)
        return False
    return True


def mark_offered(db):
    storage.set_setting(db, "desktop_offer_made", "yes")
