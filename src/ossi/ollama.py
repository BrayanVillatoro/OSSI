"""Talks to Ollama, which runs AI models on this computer. No key or account.

The address is fixed to this computer, so messages can't be sent elsewhere by accident.
Callers must not run these functions on the window's thread, because a model can take
a long time to answer.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ADDRESS = "http://127.0.0.1:11434"
RECOMMENDED_MODEL = "llama3.2"
QUICK_TIMEOUT_SECONDS = 5
START_TIMEOUT_SECONDS = 30
START_CHECK_SECONDS = 0.25
START_LOCK = threading.Lock()
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
# The longest wait for more of the answer. A model has to load before its first word, and
# this can be slow without a dedicated graphics card.
ANSWER_TIMEOUT_SECONDS = 180
# Ignores any proxy set up in Windows, which could otherwise carry messages off this computer.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ANSWER_FORMAT = {
    "type": "object",
    "properties": {"command": {"type": "string"}, "reply": {"type": "string"}},
    "required": ["command", "reply"],
}
KEEP_LOADED = "30m"  # During a conversation, so that a pause doesn't mean a slow reload.
REPLY_START = re.compile(r'"reply"\s*:\s*"')
# Stops at the closing quote, or before an escape like \n that has only partly arrived.
FINISHED_TEXT = re.compile(r'(?:[^"\\]|\\u[0-9a-fA-F]{4}|\\[^u])*')


def find_program():
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    found = shutil.which("ollama")
    if found:
        return found
    return str(default) if default.is_file() else None


def is_installed():
    return bool(find_program())


def is_running():
    try:
        with OPENER.open(f"{ADDRESS}/api/version", timeout=QUICK_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def start():
    """Start Ollama if it isn't running, and wait for it. Returns whether it's running now.

    It keeps running after OSSI closes, as it would if opened any other way, and models unload
    themselves when they aren't being used.
    """
    with START_LOCK:  # Two messages at once shouldn't start it twice.
        if is_running():
            return True
        program = find_program()
        if not program:
            return False
        try:
            subprocess.Popen(
                [program, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
                # Without this, a setting on this computer could open it up to the network.
                env={**os.environ, "OLLAMA_HOST": ADDRESS.removeprefix("http://")},
            )
        except OSError:
            return False
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if is_running():
                return True
            time.sleep(START_CHECK_SECONDS)
        return False


def starting_if_needed(work):
    """Do something that needs Ollama, first starting it if that's why it can't connect."""
    try:
        return work()
    except urllib.error.URLError:
        if not start():
            raise
    return work()


def is_local(model):
    # Ollama can also pass requests on to models it hosts online, named like "x:cloud".
    return "cloud" not in model.lower()


def list_models():
    """The models that run on this computer. Raises OSError if Ollama isn't running."""
    with OPENER.open(f"{ADDRESS}/api/tags", timeout=QUICK_TIMEOUT_SECONDS) as response:
        data = json.load(response)
    return [model["name"] for model in data["models"] if is_local(model["name"])]


def reply_so_far(content):
    """The readable part of the `reply` field in JSON that is still arriving."""
    start = REPLY_START.search(content)
    if not start:
        return ""
    finished = FINISHED_TEXT.match(content, start.end()).group()
    return json.loads(f'"{finished}"', strict=False)


def chat(model, system, message, history=(), on_text=None):
    """Ask the model, and return its answer as a dict with `command` and `reply`.

    `history` is the conversation so far, as a list of messages with `role` and `content`.
    `on_text` is called with the reply so far each time more arrives, and can raise an
    exception to stop the model.
    """
    body = {
        "format": ANSWER_FORMAT,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": message},
        ],
    }
    content = ""
    for piece in stream(model, body):
        content += piece
        if on_text:
            on_text(reply_so_far(content))
    answer = json.loads(content)
    if not isinstance(answer, dict):
        raise ValueError("The model didn't answer in the requested format")
    return answer


def talk(model, system, message, history=(), on_text=None):
    """Ask the model for a conversational answer, in plain words instead of JSON."""
    body = {
        "keep_alive": KEEP_LOADED,
        "options": {"temperature": 0.7},
        "messages": [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": message},
        ],
    }
    content = ""
    for piece in stream(model, body):
        content += piece
        if on_text:
            on_text(content)
    return content


def warm(model, system):
    """Load the model and read `system` ahead of time, so the first answer isn't the slow one."""
    body = {
        "keep_alive": KEEP_LOADED,
        "options": {"num_predict": 1},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": "Hi"}],
    }
    for _ in stream(model, body):
        pass


def stream(model, body):
    """Send a chat request to the model, and yield each piece of its answer as it arrives."""
    if not is_local(model):
        raise ValueError(f"{model} doesn't run on this computer")
    request = urllib.request.Request(
        f"{ADDRESS}/api/chat",
        data=json.dumps({"model": model, "stream": True, **body}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with OPENER.open(request, timeout=ANSWER_TIMEOUT_SECONDS) as response:
        for line in response:
            piece = json.loads(line)
            if "error" in piece:
                raise ValueError(piece["error"])
            yield piece["message"]["content"]
