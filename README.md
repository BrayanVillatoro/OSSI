# OSSI

A personal assistant desktop app that runs on your own computer. No AI key, no account, no cloud.

![The OSSI window, with tasks in a sidebar and a chat on the right](docs/screenshot.png)

## What it does

- **Daily briefing:** the first time you open the window each day, see today's reminders and
  open tasks, plus the weather if you've set a location.
- **Weather:** ask for today or tomorrow, here or anywhere else.
- **Reminders:** get a Windows notification at the time you ask for, even with the window closed.
- **Tasks:** add tasks by typing, and click a circle to complete one. Undo brings it back.
- **Notes:** save quick notes and search them later.
- **Time and date:** ask for either one.

OSSI matches what you type against known phrases and runs the skill that fits, so it only
understands what it's been taught. It still handles typos, polite wording and follow-ups like
`finish it`. Type `help` to see the main commands.

Your data stays in a local SQLite file at `~/.ossi/ossi.db`, outside this repo. Set the
`OSSI_HOME` environment variable to store it somewhere else. Before an update changes the
database's structure, OSSI saves a copy next to it, named like `ossi.db.v1.bak`.

Weather is the only feature that uses the internet, and it's off until you set a location or
ask about a specific place. See [Weather and your privacy](#weather-and-your-privacy).

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
| `briefing` or `good morning` | Shows your daily briefing |
| `turn off the daily briefing` | Stops the automatic briefing. `turn on` brings it back. |
| `set my location to 02108` | Sets your location by ZIP code, or by city like `Boston, MA` |
| `forget my location` | Removes your location and stops the automatic weather |
| `weather` or `will it rain tomorrow` | Shows the weather for your location |
| `what's the weather in Paris` | Shows the weather somewhere else |
| `remind me tomorrow at 3pm to call the bank` | Sets a reminder |
| `remind me to check the oven in 20 minutes` | Sets a reminder, with the time at the end |
| `reminders` | Lists upcoming reminders |
| `cancel reminder 2` or `cancel the oven reminder` | Cancels a reminder, by number or name |
| `start at login` | Starts OSSI in the background when you log in |
| `don't start at login` | Turns that off |
| `add task call the bank` | Adds a task |
| `tasks` | Lists open tasks |
| `done 1` or `finish buy milk` | Completes a task, by number or name |
| `undo` | Reopens the task you completed most recently |
| `note the wifi password is on the router` | Saves a note |
| `notes` | Lists your 20 most recent notes |
| `find note wifi` | Searches your notes |
| `what time is it` | Tells the time |
| `what's the date` | Tells the date |
| `help` | Lists every command |
| `exit` | Quits OSSI completely |

- **Complete a task:** click the circle next to it in the sidebar.
- **Bring a task back:** click **Undo** at the bottom of the sidebar. Click again to keep
  stepping back through earlier tasks.
- **Copy from the chat:** drag to select text and press Ctrl+C, or right-click a message and
  choose **Copy**.

### Daily briefing

The briefing appears once a day, the first time you're using the OSSI window after 5 AM. If OSSI
started in the background, it waits until you open the window. After a briefing, follow-ups like
`finish the second one` work on the tasks it listed.

### Weather and your privacy

Weather comes from [Open-Meteo](https://open-meteo.com/), a free service that needs no key or
account. OSSI sends it the place you typed to find its coordinates, then those coordinates to get
the forecast. As with any website, the service can also see your IP address. Your tasks, notes and
reminders are never sent.

With a location set, OSSI checks the weather for each briefing and when you ask. It doesn't check
in the background otherwise. With no location set, it only connects when you name a place, like
`weather in Paris`. Temperatures are in Fahrenheit for US locations and Celsius elsewhere.

### Talking to OSSI

You don't have to type commands exactly:

- **Typos:** type `add taks call the bank` and OSSI asks `Did you mean: add task call the bank?`
  Answer `yes` or `no`. It never runs a correction without asking.
- **Polite wording:** `hey ossi, can you show my tasks please` works.
- **Follow-ups:** after adding a task, `finish it` completes that task. After setting a reminder,
  `cancel it` cancels that reminder. After listing tasks, `finish the second one` works.
- **Names:** `finish milk` finds the task with "milk" in it, even with a small typo. If several
  match, OSSI asks which one you mean.
- **Missing details:** `remind me to pay rent` makes OSSI ask when. Say `never mind` to drop a
  question, or type any other command to move on.

OSSI only remembers follow-ups while it's running, and the single commands you run in a
terminal can't answer its questions.

### Reminders

OSSI confirms the exact time it understood, so you can catch a mistake right away. It accepts:

- **Amounts of time:** `in 20 minutes`, `in an hour`, `in 1 hour and 30 minutes`, `in 3 days`
- **Times:** `at 3pm`, `at 15:30`, `at noon`. `at 3` means the next 3 o'clock that makes sense.
- **Days:** `tomorrow`, `tonight`, `friday`, `next friday`, `tomorrow morning`
- **Dates:** `november 3`, `nov 3rd at 2pm`, `11/3` (month first)

A day with no time means 9 AM.

### Running in the background

Reminders only fire while OSSI is running, so closing the window hides it instead of quitting.
Open OSSI the usual way to bring the window back, and type `exit` to quit completely. There's
no system tray icon yet.

To keep reminders working across restarts, type `start at login`. Anything that came due while
OSSI wasn't running appears as a missed reminder the next time it starts. If Windows blocks a
notification with Do Not Disturb, the reminder still appears in the chat.

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
  router.py     Matches text to a command and tracks the conversation
  fuzzy.py      Works out what a mistyped command meant
  references.py Works out what "it" or "the second one" means
  registry.py   The @command decorator skills use
  storage.py    SQLite connection and database upgrades
  when.py       Understands times like "tomorrow at 3pm"
  weather.py    Gets the forecast from Open-Meteo
  notifications.py  Windows notifications
  instance.py   Keeps one copy running and reopens its window
  autostart.py  Starts OSSI at login
  skills/       One file per skill, loaded automatically
  assets/       The app icons
scripts/        Generate the Desktop shortcut and the icons
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

The window tests open a hidden window, so they need a desktop session.

## Roadmap

1. **Done:** desktop app, tasks, notes, time and date, reminders, typos and follow-ups, daily
   briefing, weather
2. **Computer control:** open apps and websites, organize the Downloads folder
3. **Local AI (optional):** a model running through Ollama handles whatever the patterns
   don't match, still with no key. `router.handle` is where it plugs in.
4. **Voice:** offline speech recognition and text to speech

## License

See [LICENSE](LICENSE).
