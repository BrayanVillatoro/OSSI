# Development

## Run from a terminal

The app doesn't need this. Install OSSI if you also want the terminal commands:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

```powershell
ossi                          # open the window
ossi-gui                      # open the window and free up the terminal
ossi add task call the bank   # run one command without opening the window
```

If a command asks a question, like `Did you mean`, type your answer at the `>` prompt. Press Enter
on an empty line to skip it.

## Where data is kept

Data is in a SQLite file at `~/.ossi/ossi.db`. Set the `OSSI_HOME` environment variable to store it
somewhere else. Before an update changes the database's structure, OSSI saves a copy next to it,
named like `ossi.db.v1.bak`.

## The voice add-on

OSSI itself has no dependencies, so talking runs in a separate program. Clicking **Talk** the first
time creates a Python environment in `~/.ossi/voice/`, installs pinned packages and downloads the
models. `voice_worker.py` runs in that environment and exchanges lines of JSON with `talk.py`, and
it runs offline once installed. Deleting the folder removes the add-on. Errors go to
`~/.ossi/voice/voice.log`.

It uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) to recognize speech,
[Kokoro](https://github.com/thewh1teagle/kokoro-onnx) for the voice, and
[sounddevice](https://python-sounddevice.readthedocs.io/) for the microphone and speakers.

## Project layout

```
OSSI.pyw        Double-click to open the app
src/ossi/
  gui.py        Desktop window
  settings_panel.py  The Settings page
  theme.py      The window's colors and fonts
  drawing.py    Smooth rounded shapes for the window
  cli.py        Terminal commands
  router.py     Matches text to a command and tracks the conversation
  fuzzy.py      Works out what a mistyped command meant
  references.py Works out what "it" or "the second one" means
  registry.py   The @command decorator skills use
  storage.py    SQLite connection and database upgrades
  when.py       Understands times like "tomorrow at 3pm"
  weather.py    Gets the forecast from Open-Meteo
  ollama.py     Talks to a local AI model
  talk.py       Runs a spoken conversation in the window
  voice.py      Installs the voice add-on and starts its worker
  voice_worker.py  Listens and speaks, using the add-on's own Python environment
  notifications.py  Windows notifications
  instance.py   Keeps one copy running, and replaces it after an update
  autostart.py  Starts OSSI at login
  desktop.py    Adds OSSI to the Desktop with its icon
  skills/       One file per skill, loaded automatically
  assets/       The app icons
scripts/        Generates the icons
tests/          Unit tests
docs/           This page and the README's screenshots
```

## Add a skill

Create a file in `src/ossi/skills/`. It's picked up automatically the next time OSSI starts:

```python
import random

from ossi.registry import command


@command("flip a coin", r"flip a coin")
def flip(db):
    return random.choice(["Heads.", "Tails."])
```

- The first argument to `@command` is the text shown by `help`. The rest are patterns, and
  one of them must match the whole message, ignoring case.
- The function receives the database connection and returns the reply as a string.
- Named groups in a pattern, like `(?P<title>.+)`, are passed to the function as arguments.
- Typo correction picks up the words in your patterns automatically.
- To continue a conversation, add a `session` parameter to the function and return
  `Ask(question, answer_function)` from `ossi.registry` instead of a string. Skills can also
  remember what "it" means in `session.focus`. See `skills/reminders.py` for an example.
- For anything slow, like using the internet, return `Fetch(prompt, work, finish)` so the window
  doesn't freeze. See `skills/weather.py` for an example.
- If a pattern is broad enough to swallow other skills' commands, pass `catch_all=True` to
  `@command` so that it's tried last.

If a skill needs to change the database's structure, add a new entry to the end of `MIGRATIONS`
in `storage.py`. Don't edit an existing entry, because existing databases have already run it.

## Tests

```powershell
python -m unittest discover -s tests
```

The window tests need a desktop session. Most keep their window hidden, but a few show it briefly.
They use temporary data and never change your login settings. Nothing in the tests uses the
internet.
