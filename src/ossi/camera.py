"""Installs the optional camera add-on, and takes pictures using camera_worker.py.

The worker runs in the add-on's own Python environment, in one folder next to OSSI's database, so
OSSI itself keeps no dependencies. The model that understands the pictures is kept by Ollama.
"""

import json
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from ossi import ollama, storage

VERSION = "1"  # Changing this makes everyone install the add-on again.
PACKAGES = ["opencv-python-headless==4.12.0.88"]
WORKER = Path(__file__).with_name("camera_worker.py")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
# Enough to try every camera, if the first ones are dark.
TAKE_TIMEOUT_SECONDS = 45
MODEL_SIZE_GB = 3.4
BYTES_PER_GB = 1000**3

NO_CAMERA = (
    "I couldn't get a picture from a camera. Check that your webcam is plugged in and that no other "
    "app is using it. If it still doesn't work, open Windows Settings, go to Privacy & security, "
    "then Camera, and let desktop apps use it."
)
TOO_DARK = "The picture came out completely dark. Is something covering the camera?"
NOT_ANSWERING = "The camera didn't respond. Unplugging it and plugging it back in may help."
STOPPED = "The camera stopped working. It may have been unplugged, or another app may be using it."
PROBLEMS = {"no_camera": NO_CAMERA, "too_dark": TOO_DARK, "gone": STOPPED}


class CameraError(Exception):
    """A picture couldn't be taken. The message says why, in words for the user."""


@dataclass(eq=False)
class Photo:
    """A picture in memory, as base64 text. It's never written to a file."""

    picture: str  # For the model.
    thumbnail: str  # For the chat.


def folder():
    return storage.default_path().parent / "camera"


def log_path():
    return folder() / "camera.log"


def python_path():
    return folder() / "python" / "Scripts" / "python.exe"


def marker_path():
    return folder() / "installed"


def model_is_ours_path():
    """Exists if OSSI downloaded the model, so that removing the add-on should remove it too."""
    return folder() / "downloaded_model"


def is_installed():
    return marker_path().is_file() and marker_path().read_text().strip() == VERSION


def run_logged(command):
    with log_path().open("a", encoding="utf-8") as log:
        subprocess.run(
            command, stdout=log, stderr=log, stdin=subprocess.DEVNULL, check=True,
            creationflags=NO_WINDOW,
        )


def get_model(progress):
    def report(downloaded):
        progress(
            "Setting up the camera: downloading the vision model, "
            f"{downloaded / BYTES_PER_GB:.1f} of about {MODEL_SIZE_GB} GB..."
        )

    def work():
        if ollama.VISION_MODEL not in ollama.all_models():
            model_is_ours_path().touch()
            ollama.pull(ollama.VISION_MODEL, report)

    ollama.starting_if_needed(work)


def install(progress):
    """Set up the add-on, reporting each step. Slow, so it can't run on the window's thread."""
    folder().mkdir(parents=True, exist_ok=True)
    marker_path().unlink(missing_ok=True)
    # The windowed launcher runs pythonw, which can't show pip's output or start the venv module.
    system_python = str(Path(sys.executable).with_name("python.exe"))
    environment = folder() / "python"

    progress("Setting up the camera: preparing...")
    shutil.rmtree(environment, ignore_errors=True)
    run_logged([system_python, "-m", "venv", str(environment)])

    progress("Setting up the camera: installing the camera package...")
    run_logged(
        [str(python_path()), "-m", "pip", "install", "--disable-pip-version-check", *PACKAGES]
    )
    run_logged([str(python_path()), "-I", str(WORKER), "--check"])

    progress("Setting up the camera: downloading the vision model...")
    get_model(progress)
    marker_path().write_text(VERSION)


def remove():
    """Delete the add-on, and the model if OSSI downloaded it. Returns False if the model stayed."""
    ours = model_is_ours_path().is_file()
    marker_path().unlink(missing_ok=True)
    shutil.rmtree(folder(), ignore_errors=True)
    if not ours:
        return True
    try:
        ollama.starting_if_needed(lambda: ollama.delete_model(ollama.VISION_MODEL))
    except (OSError, ValueError):
        return False
    return True


def take_photo():
    """Turn on the camera for a moment and return a Photo. Raises CameraError if it can't."""
    try:
        with log_path().open("a", encoding="utf-8") as log:
            done = subprocess.run(
                [str(python_path()), "-I", str(WORKER)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
                encoding="utf-8",
                timeout=TAKE_TIMEOUT_SECONDS,
                creationflags=NO_WINDOW,
            )
        answer = json.loads(done.stdout)
    except subprocess.TimeoutExpired as error:
        raise CameraError(NOT_ANSWERING) from error
    except (OSError, ValueError) as error:
        raise CameraError(
            f"The camera add-on isn't working. There may be more detail in {log_path()}."
        ) from error
    if "error" in answer:
        raise CameraError(PROBLEMS[answer["error"]])
    return Photo(answer["picture"], answer["thumbnail"])


class LiveCamera:
    """The worker keeping the camera on, for a live view. `latest` is the newest view, as base64
    text, and `problem` says why it stopped. Older views are dropped if the window falls behind."""

    def __init__(self):
        self.latest = None
        self.problem = None
        self.pictures = queue.Queue()
        self.log = log_path().open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(python_path()), "-I", str(WORKER), "--live"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            encoding="utf-8",
            bufsize=1,
            creationflags=NO_WINDOW,
        )
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue  # Something other than a message reached the output.
            if message["type"] == "view":
                self.latest = message["view"]
            elif message["type"] == "picture":
                self.pictures.put(message)
            elif message["type"] == "error":
                self.problem = message["error"]
        self.problem = self.problem or "gone"
        self.pictures.put({"error": self.problem})

    def take_photo(self):
        """Return a Photo of what's in view. Slow, so it can't run on the window's thread."""
        try:
            self.process.stdin.write(json.dumps({"type": "take"}) + "\n")
            self.process.stdin.flush()
            answer = self.pictures.get(timeout=TAKE_TIMEOUT_SECONDS)
        except (OSError, ValueError) as error:
            raise CameraError(STOPPED) from error
        except queue.Empty as error:
            raise CameraError(NOT_ANSWERING) from error
        if "error" in answer:
            raise CameraError(PROBLEMS[answer["error"]])
        return Photo(answer["picture"], answer["thumbnail"])

    def close(self):
        try:
            self.process.stdin.close()  # Which tells the worker to turn off the camera and exit.
        except OSError:
            pass
        threading.Thread(target=self.wait_then_stop, daemon=True).start()

    def wait_then_stop(self):
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()
