"""Starts OSSI in the background at login, using a per-user Windows setting."""

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "OSSI"
LAUNCHER = Path(__file__).resolve().parents[2] / "OSSI.pyw"


def supported():
    return sys.platform == "win32"


def command_line():
    python = Path(sys.executable)
    windowed = python.with_name("pythonw.exe")
    if windowed.exists():
        python = windowed
    if LAUNCHER.exists():
        return f'"{python}" "{LAUNCHER}" --background'
    return f'"{python}" -m ossi --background'


def enable():
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command_line())


def disable():
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass


def is_enabled():
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        try:
            winreg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return False
    return True
