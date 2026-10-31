"""Installs the optional voice add-on, and runs voice_worker.py in its Python environment.

Everything goes in one folder next to OSSI's database, so OSSI itself keeps no dependencies and
deleting that folder removes the add-on completely.
"""

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from ossi import storage

VERSION = "1"  # Changing this makes everyone install the add-on again.
PACKAGES = ["faster-whisper==1.2.1", "kokoro-onnx==0.6.1", "sounddevice==0.5.6"]
MODELS_ADDRESS = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
VOICE_FILES = {
    "kokoro-v1.0.int8.onnx": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
WORKER = Path(__file__).with_name("voice_worker.py")
VOICES = {
    "Heart": "af_heart",
    "Bella": "af_bella",
    "Michael": "am_michael",
    "Emma": "bf_emma",
    "George": "bm_george",
}
DEFAULT_VOICE = "Bella"
SAMPLE_LINE = "Hi, I'm {name}. This is how I'd sound when we talk."
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
READ_SIZE = 1 << 20


def folder():
    return storage.default_path().parent / "voice"


def log_path():
    return folder() / "voice.log"


def python_path():
    return folder() / "python" / "Scripts" / "python.exe"


def marker_path():
    return folder() / "installed"


def is_installed():
    return marker_path().is_file() and marker_path().read_text().strip() == VERSION


def run_logged(command):
    with log_path().open("a", encoding="utf-8") as log:
        subprocess.run(
            command, stdout=log, stderr=log, stdin=subprocess.DEVNULL, check=True,
            creationflags=NO_WINDOW,
        )


def download(name, expected, target):
    partial = target.with_suffix(".partial")
    digest = hashlib.sha256()
    with urllib.request.urlopen(MODELS_ADDRESS + name, timeout=60) as response:
        with partial.open("wb") as file:
            while piece := response.read(READ_SIZE):
                digest.update(piece)
                file.write(piece)
    if digest.hexdigest() != expected:
        partial.unlink()
        raise ValueError(f"{name} didn't download correctly")
    partial.replace(target)


def install(progress):
    """Set up the add-on, reporting each step. Slow, so it can't run on the window's thread."""
    folder().mkdir(parents=True, exist_ok=True)
    marker_path().unlink(missing_ok=True)
    # The windowed launcher runs pythonw, which can't show pip's output or start the venv module.
    system_python = str(Path(sys.executable).with_name("python.exe"))
    environment = folder() / "python"

    progress("Setting up voice: preparing...")
    shutil.rmtree(environment, ignore_errors=True)
    run_logged([system_python, "-m", "venv", str(environment)])

    progress("Setting up voice: installing packages...")
    run_logged(
        [str(python_path()), "-m", "pip", "install", "--disable-pip-version-check", *PACKAGES]
    )

    progress("Setting up voice: downloading the voice...")
    models = folder() / "models"
    models.mkdir(exist_ok=True)
    for name, expected in VOICE_FILES.items():
        target = models / name
        if not target.is_file():
            download(name, expected, target)

    progress("Setting up voice: downloading speech recognition...")
    run_logged([str(python_path()), "-I", str(WORKER), "--prepare", str(models)])
    marker_path().write_text(VERSION)


def remove():
    marker_path().unlink(missing_ok=True)
    shutil.rmtree(folder(), ignore_errors=True)


def play_sample(name):
    """Start reading a line in this voice, and return the running process."""
    text = SAMPLE_LINE.format(name=name)
    with log_path().open("a", encoding="utf-8") as log:
        return subprocess.Popen(
            [
                str(python_path()),
                "-I",
                str(WORKER),
                "--sample",
                str(folder() / "models"),
                VOICES[name],
                text,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            creationflags=NO_WINDOW,
        )


class VoiceProcess:
    """The running worker. Its messages wait in `events` for the window's thread to read."""

    def __init__(self, voice_name):
        self.events = queue.Queue()
        folder().mkdir(parents=True, exist_ok=True)
        self.log = log_path().open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(python_path()), "-I", str(WORKER), str(folder() / "models"), VOICES[voice_name]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            encoding="utf-8",
            bufsize=1,
            creationflags=NO_WINDOW,
            env={**os.environ, "HF_HUB_DISABLE_TELEMETRY": "1"},
        )
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                pass  # Something other than a message reached the output.
        self.events.put({"type": "gone"})

    def send(self, **message):
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass  # It has already exited, which the "gone" event reports.

    def close(self):
        self.send(type="quit")
        try:
            self.process.stdin.close()
        except OSError:
            pass
        threading.Thread(target=self.wait_then_stop, daemon=True).start()

    def wait_then_stop(self):
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()
