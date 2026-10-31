"""Updates OSSI to the newest release published on GitHub, when someone asks it to.

Only OSSI's own files change. Tasks, notes, settings and the voice add-on live elsewhere, in
~/.ossi. The previous version's files stay in a folder next to the new ones until the next update.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ossi import __version__, autostart

REPOSITORY = "BrayanVillatoro/OSSI"
LATEST_RELEASE = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASE_FILES = "https://github.com/" + REPOSITORY + "/archive/refs/tags/{tag}.zip"
TIMEOUT_SECONDS = 30
TRY_IT_TIMEOUT_SECONDS = 60
READ_SIZE = 1 << 20
MOST_BYTES = 200 * READ_SIZE  # Far more than OSSI needs, so a bad download can't fill the disk.
WORKING_FOLDER = ".update_download"
PREVIOUS_FOLDER = ".previous_version"
TAG = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
VERSION_LINE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
OPENER = urllib.request.build_opener()
HEADERS = {"User-Agent": "OSSI", "Accept": "application/vnd.github+json"}


class UpdateError(Exception):
    """Something went wrong, and OSSI's files are as they were. The message is for the user."""


@dataclass
class Release:
    version: str
    tag: str
    page: str


def install_folder():
    return autostart.LAUNCHER.parent


def version_parts(text):
    match = TAG.fullmatch(text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def blocked_reason(folder=None):
    """Why this copy can't update itself, or None if it can."""
    folder = folder or install_folder()
    if not (folder / "OSSI.pyw").is_file():
        return "This copy was installed as a Python package, so update it with pip."
    if (folder / ".git").exists():
        return "This copy is managed with git, so update it with git pull."
    return None


def check():
    """Return the newest Release if it's newer than this copy, or else False."""
    request = urllib.request.Request(LATEST_RELEASE, headers=HEADERS)
    try:
        with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:  # Nothing has been published yet.
            return False
        raise
    tag = data["tag_name"]
    newest = version_parts(tag)
    if not newest or newest <= version_parts(__version__):
        return False
    return Release(".".join(map(str, newest)), tag, data["html_url"])


def download(release, target):
    request = urllib.request.Request(
        RELEASE_FILES.format(tag=release.tag), headers={"User-Agent": "OSSI"}
    )
    size = 0
    with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response, target.open("wb") as file:
        while piece := response.read(READ_SIZE):
            size += len(piece)
            if size > MOST_BYTES:
                raise UpdateError("The update was much larger than expected, so I stopped.")
            file.write(piece)


def unpack(archive, target):
    """Unpack the download, and return the folder inside it that has OSSI's files."""
    try:
        with zipfile.ZipFile(archive) as files:
            total = 0
            for entry in files.infolist():
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or ":" in entry.filename:
                    raise UpdateError("The update contained an unsafe file, so I stopped.")
                total += entry.file_size
            if total > MOST_BYTES:
                raise UpdateError("The update was much larger than expected, so I stopped.")
            files.extractall(target)
    except zipfile.BadZipFile:
        raise UpdateError("The update didn't download correctly. Try again.") from None
    folders = list(target.iterdir())
    if len(folders) != 1 or not folders[0].is_dir():
        raise UpdateError("The update wasn't laid out as expected, so I stopped.")
    return folders[0]


def verify(new_files, release):
    """Make sure this really is the expected version of OSSI, and that it can start."""
    version_file = new_files / "src" / "ossi" / "__init__.py"
    needed = [new_files / "OSSI.pyw", new_files / "src" / "ossi" / "gui.py", version_file]
    if not all(path.is_file() for path in needed):
        raise UpdateError("The update was missing some of OSSI's files, so I stopped.")
    found = VERSION_LINE.search(version_file.read_text(encoding="utf-8"))
    if not found or version_parts(found.group(1)) != version_parts(release.version):
        raise UpdateError("The update's version didn't match, so I stopped.")
    # The windowed launcher runs pythonw, which has no output to report a failure with.
    python = Path(sys.executable).with_name("python.exe")
    result = subprocess.run(
        [
            str(python if python.exists() else sys.executable),
            "-I",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import ossi.gui, ossi.router",
            str(new_files / "src"),
        ],
        cwd=new_files,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=TRY_IT_TIMEOUT_SECONDS,
        creationflags=NO_WINDOW,
    )
    if result.returncode:
        raise UpdateError("The update wouldn't start on this computer, so I kept this version.")


def remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def swap_in(new_files, folder):
    """Move the new files into place, or put everything back if any of them can't be."""
    previous = folder / PREVIOUS_FOLDER
    shutil.rmtree(previous, ignore_errors=True)
    previous.mkdir()
    moved_aside = []
    placed = []
    try:
        for item in sorted(new_files.iterdir()):
            target = folder / item.name
            if target.exists():
                os.replace(target, previous / item.name)
                moved_aside.append(item.name)
            os.replace(item, target)
            placed.append(item.name)
    except OSError:
        for name in placed:
            remove(folder / name)
        for name in moved_aside:
            os.replace(previous / name, folder / name)
        raise UpdateError(
            "I couldn't replace OSSI's files, so I kept this version. Is it in a protected folder?"
        ) from None


def install(release, folder=None):
    """Download the release and put it in place. Slow, so not for the window's thread."""
    folder = folder or install_folder()
    working = folder / WORKING_FOLDER
    shutil.rmtree(working, ignore_errors=True)
    try:
        working.mkdir()
        archive = working / "update.zip"
        download(release, archive)
        new_files = unpack(archive, working / "new")
        verify(new_files, release)
        swap_in(new_files, folder)
    finally:
        shutil.rmtree(working, ignore_errors=True)


def start_new_copy():
    """Open the updated OSSI. It sees that it's newer, so this copy closes and hands over."""
    python, arguments = autostart.launch_parts()
    subprocess.Popen(
        f'"{python}" {arguments}',
        cwd=install_folder(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
