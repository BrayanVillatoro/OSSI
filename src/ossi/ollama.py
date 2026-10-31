"""Talks to Ollama, which runs AI models on this computer. No key or account.

The address is fixed to this computer, so messages can't be sent elsewhere by accident.
Callers must not run these functions on the window's thread, because a model can take
a long time to answer.
"""

import json
import os
import shutil
import urllib.request
from pathlib import Path

ADDRESS = "http://127.0.0.1:11434"
RECOMMENDED_MODEL = "llama3.2"
QUICK_TIMEOUT_SECONDS = 5
# A model has to load first, and this can be slow without a dedicated graphics card.
ANSWER_TIMEOUT_SECONDS = 180
# Ignores any proxy set up in Windows, which could otherwise carry messages off this computer.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ANSWER_FORMAT = {
    "type": "object",
    "properties": {"command": {"type": "string"}, "reply": {"type": "string"}},
    "required": ["command", "reply"],
}


def is_installed():
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return bool(shutil.which("ollama")) or default.is_file()


def is_local(model):
    # Ollama can also pass requests on to models it hosts online, named like "x:cloud".
    return "cloud" not in model.lower()


def list_models():
    """The models that run on this computer. Raises OSError if Ollama isn't running."""
    with OPENER.open(f"{ADDRESS}/api/tags", timeout=QUICK_TIMEOUT_SECONDS) as response:
        data = json.load(response)
    return [model["name"] for model in data["models"] if is_local(model["name"])]


def chat(model, system, message, history=()):
    """Ask the model, and return its answer as a dict with `command` and `reply`.

    `history` is the conversation so far, as a list of messages with `role` and `content`.
    """
    if not is_local(model):
        raise ValueError(f"{model} doesn't run on this computer")
    body = {
        "model": model,
        "stream": False,
        "format": ANSWER_FORMAT,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": message},
        ],
    }
    request = urllib.request.Request(
        f"{ADDRESS}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with OPENER.open(request, timeout=ANSWER_TIMEOUT_SECONDS) as response:
        data = json.load(response)
    answer = json.loads(data["message"]["content"])
    if not isinstance(answer, dict):
        raise ValueError("The model didn't answer in the requested format")
    return answer
