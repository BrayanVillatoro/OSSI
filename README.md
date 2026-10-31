# OSSI

A personal assistant desktop app that runs entirely on your own computer. No AI key, no account, no cloud.

![The OSSI window, with tasks in a sidebar and a chat on the right](docs/screenshot.png)

## What it does

- **Tasks:** add tasks by typing, and click a circle to complete one. Undo brings it back.
- **Notes:** save quick notes and search them later.
- **Time and date:** ask for either one.

OSSI matches what you type against known phrases and runs the skill that fits, so it only
understands what it's been taught. Type `help` to see every command.

Your data stays in a local SQLite file at `~/.ossi/ossi.db`, outside this repo. Set the
`OSSI_HOME` environment variable to store it somewhere else.

## Get started

You need Windows and Python 3.11 or newer (tested on 3.12). OSSI has no other dependencies.

Double-click `OSSI.pyw` to open the app. There's nothing to install.

To add a Desktop shortcut with OSSI's icon, run this once. Run it again if you move the repo.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
```

## Using the app

Type in the box at the bottom of the window and press Enter.

| Type | What happens |
| --- | --- |
| `add task call the bank` | Adds a task |
| `tasks` | Lists open tasks |
| `done 1` | Completes task 1 |
| `undo` | Reopens the task you completed most recently |
| `note the wifi password is on the router` | Saves a note |
| `notes` | Lists your 20 most recent notes |
| `find note wifi` | Searches your notes |
| `what time is it` | Tells the time |
| `what's the date` | Tells the date |
| `help` | Lists every command |
| `exit` | Closes the window |

- **Complete a task:** click the circle next to it in the sidebar.
- **Bring a task back:** click **Undo** at the bottom of the sidebar. Click again to keep
  stepping back through earlier tasks.
- **Copy from the chat:** drag to select text and press Ctrl+C, or right-click a message and
  choose **Copy**.

## Run from a terminal (optional)

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

## Project layout

```
OSSI.pyw        Double-click to open the app
src/ossi/
  gui.py        Desktop window, including its colors
  drawing.py    Smooth rounded shapes for the window
  cli.py        Terminal commands
  router.py     Matches text to a command
  registry.py   The @command decorator skills use
  storage.py    SQLite connection and tables
  skills/       One file per skill, loaded automatically
  assets/       The app icon
scripts/        Generate the Desktop shortcut and the icon
tests/          Unit tests
docs/           The screenshot in this README
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

## Tests

```powershell
python -m unittest discover -s tests
```

The window tests open a hidden window, so they need a desktop session.

## Roadmap

1. **Done:** desktop app, tasks, notes, time and date
2. **Reminders:** due dates and Windows notifications
3. **Daily briefing:** today's tasks and reminders, plus weather from a keyless service
4. **Computer control:** open apps and websites, organize the Downloads folder
5. **Voice:** offline speech recognition and text to speech
6. **Local AI (optional):** a model running through Ollama handles whatever the patterns
   don't match, still with no key. `router.handle` is where it plugs in.

## License

See [LICENSE](LICENSE).
