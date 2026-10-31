"""Windows notifications, sent through PowerShell because Python has no built-in way."""

import os
import re
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

APP_ID = "OSSI"
ICON = Path(__file__).parent / "assets" / "ossi.png"

# The text arrives in an environment variable, so it can't run as PowerShell code.
SCRIPT = """
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:OSSI_TOAST_XML)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:OSSI_TOAST_APP).Show($toast)
"""


def toast_xml(title, body):
    return (
        '<toast duration="long"><visual><binding template="ToastGeneric">'
        f"<text>{clean(title)}</text><text>{clean(body)}</text>"
        '</binding></visual><audio src="ms-winsoundevent:Notification.Reminder"/></toast>'
    )


def clean(text):
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text))


def register_app():
    """Tell Windows the name and icon to show. This needs no admin rights."""
    import winreg

    key = rf"Software\Classes\AppUserModelId\{APP_ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, "DisplayName", 0, winreg.REG_SZ, "OSSI")
        winreg.SetValueEx(handle, "IconUri", 0, winreg.REG_SZ, str(ICON))


def send(title, body):
    """Start showing a notification without waiting for it.

    Returns the running process, whose exit code says whether it worked, or None if
    it couldn't even start.
    """
    if sys.platform != "win32":
        return None
    try:
        register_app()
        env = {**os.environ, "OSSI_TOAST_XML": toast_xml(title, body), "OSSI_TOAST_APP": APP_ID}
        return subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, ValueError):
        return None
