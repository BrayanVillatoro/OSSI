"""The Settings page. Its controls call the same skill functions the typed commands do."""

import tkinter as tk
import webbrowser
from tkinter import ttk

from ossi import autostart, desktop, ollama, storage, voice, weather
from ossi.drawing import rounded_image
from ossi.registry import Fetch
from ossi.skills import ai, briefing, startup
from ossi.skills import weather as weather_skill
from ossi.theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    BORDER,
    FAMILY,
    FONT,
    MUTED,
    ON_ACCENT,
    SMALL,
    SMALL_BOLD,
    SURFACE,
    TEXT,
    add_scrollbar,
)

OLLAMA_SITE = "https://ollama.com/"
BUG_REPORT_PAGE = "https://github.com/BrayanVillatoro/OSSI/issues/new"
PULL_COMMAND = f"ollama pull {ollama.RECOMMENDED_MODEL}"
PASTE_STEP = "Open Command Prompt (search for cmd in the Start menu), press Ctrl+V, then Enter."
FINISH_STEP = "The download is about 2 GB. When it says success, click Try again."
SWITCH_WIDTH = 44
SWITCH_HEIGHT = 24
KNOB = 18
SIDE_MARGIN = 28
RIGHT_SPACE = 90  # Room beside wrapped text for the switch.
LABEL_INSET = 3  # Lines a box up with label text, which Tk indents slightly.
SAMPLE_CHECK_MS = 200


class Switch(tk.Canvas):
    """An on/off switch. `command(on)` runs when someone flips it."""

    def __init__(self, parent, command):
        super().__init__(
            parent,
            width=SWITCH_WIDTH,
            height=SWITCH_HEIGHT,
            bg=BG,
            highlightthickness=1,
            highlightbackground=BG,
            highlightcolor=ACCENT,
            borderwidth=0,
            takefocus=1,
            cursor="hand2",
        )
        self.command = command
        self.on = False
        self.images = {}
        for state, track, knob in ((True, ACCENT, ON_ACCENT), (False, BORDER, TEXT)):
            self.images[state] = (
                rounded_image(SWITCH_WIDTH, SWITCH_HEIGHT, SWITCH_HEIGHT // 2, track, BG),
                rounded_image(KNOB, KNOB, KNOB // 2, knob, track),
            )
        self.track = self.create_image(1, 1, anchor="nw")
        self.knob = self.create_image(0, 0, anchor="nw")
        self.bind("<Button-1>", lambda event: self.flip())
        self.bind("<space>", lambda event: self.flip())
        self.set(False)

    def set(self, on):
        self.on = bool(on)
        track, knob = self.images[self.on]
        self.itemconfigure(self.track, image=track)
        self.itemconfigure(self.knob, image=knob)
        margin = (SWITCH_HEIGHT - KNOB) // 2
        self.coords(self.knob, 1 + (SWITCH_WIDTH - KNOB - margin if self.on else margin), 1 + margin)

    def flip(self):
        self.focus_set()
        self.set(not self.on)
        self.command(self.on)


class SettingsPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        self.visible = False
        self.wrapped = []
        self.ai_check = 0  # Only the newest check's answer counts.
        self.place_lookup = 0
        self.sample = None  # The voice sample playing right now.

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        tk.Label(self, text="Settings", bg=BG, fg=TEXT, font=(FAMILY, 16, "bold")).grid(
            row=0, column=0, sticky="w", padx=SIDE_MARGIN, pady=(18, 8)
        )

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, borderwidth=0, width=1, height=1)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        add_scrollbar(self, self.canvas, row=1, column=1)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.body.columnconfigure(0, weight=1)
        self.body_item = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.canvas.bind("<Configure>", self.fit_width)
        self.body.bind(
            "<Configure>",
            lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )

        self.build_ai()
        self.build_talk()
        self.build_weather()
        self.build_general()
        # Hidden only now: earlier, the next section would have been given their rows.
        self.ai_links.grid_remove()
        self.model_row.grid_remove()
        self.use_wheel_on(self.canvas)
        self.use_wheel_on(self.body)

    def fit_width(self, event):
        self.canvas.itemconfigure(self.body_item, width=event.width)
        for label in self.wrapped:
            label.configure(wraplength=max(event.width - 2 * SIDE_MARGIN - RIGHT_SPACE, 200))

    def scroll(self, event):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def use_wheel_on(self, widget):
        # A dropdown keeps the wheel for itself, to change its value.
        if not isinstance(widget, ttk.Combobox):
            widget.bind("<MouseWheel>", self.scroll)
        for child in widget.winfo_children():
            self.use_wheel_on(child)

    def heading(self, text):
        tk.Label(self.body, text=text, bg=BG, fg=MUTED, font=SMALL_BOLD, anchor="w").grid(
            sticky="ew", padx=SIDE_MARGIN, pady=(16, 6)
        )

    def note(self, parent, text="", color=MUTED):
        label = tk.Label(
            parent, text=text, bg=BG, fg=color, font=SMALL, anchor="w", justify="left", wraplength=400
        )
        self.wrapped.append(label)
        return label

    def switch_row(self, title, description, command):
        row = tk.Frame(self.body, bg=BG)
        row.columnconfigure(0, weight=1)
        row.grid(sticky="ew", padx=SIDE_MARGIN, pady=(0, 10))
        tk.Label(row, text=title, bg=BG, fg=TEXT, font=FONT, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        description_label = self.note(row, description)
        description_label.grid(row=1, column=0, sticky="w")
        switch = Switch(row, command)
        switch.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
        return switch, description_label

    def clear(self, frame):
        for child in frame.winfo_children():
            child.destroy()
        # An emptied frame would otherwise keep its old size and leave a gap.
        frame.grid_remove()

    def links(self, frame, items):
        """Replace a row of links with these (text, function) pairs."""
        self.clear(frame)
        if items:
            frame.grid()
        for column, (text, function) in enumerate(items):
            link = tk.Label(frame, text=text, bg=BG, fg=ACCENT, font=SMALL, cursor="hand2")
            link.grid(row=0, column=column, padx=(0, 16), sticky="w")
            link.bind("<Button-1>", lambda event, run=function: run())
            link.bind("<Enter>", lambda event, w=link: w.configure(fg=ACCENT_HOVER))
            link.bind("<Leave>", lambda event, w=link: w.configure(fg=ACCENT))
            self.use_wheel_on(link)

    # Local AI

    def build_ai(self):
        self.heading("LOCAL AI")
        self.ai_switch, self.ai_status = self.switch_row(
            "Use a local AI model", "", self.toggle_ai
        )
        self.ai_links = tk.Frame(self.body, bg=BG)
        self.ai_links.grid(sticky="w", padx=SIDE_MARGIN, pady=(0, 8))
        self.model_row = tk.Frame(self.body, bg=BG)
        self.model_row.grid(sticky="w", padx=SIDE_MARGIN, pady=(0, 6))
        tk.Label(self.model_row, text="Model", bg=BG, fg=MUTED, font=SMALL).grid(
            row=0, column=0, padx=(0, 10)
        )
        self.model_box = ttk.Combobox(self.model_row, state="readonly", font=SMALL, width=30)
        self.model_box.grid(row=0, column=1)
        self.model_box.bind("<<ComboboxSelected>>", lambda event: self.choose_model())

    def set_ai_status(self, text, links=()):
        self.ai_status.configure(text=text)
        self.links(self.ai_links, links)

    def refresh_ai(self):
        model = storage.get_setting(self.db, "ai_model")
        self.ai_switch.set(model)
        self.model_row.grid_remove()
        if model:
            self.check_ollama(turning_on=False)
        else:
            self.ai_check += 1
            self.set_ai_status(
                "Off. A model on this computer can handle messages OSSI doesn't understand."
            )

    def toggle_ai(self, on):
        if on:
            self.check_ollama(turning_on=True)
        else:
            ai.turn_off(self.db, self.app.session)
            self.refresh_ai()

    def check_ollama(self, turning_on):
        self.ai_check += 1
        check = self.ai_check
        self.set_ai_status("Looking for Ollama...")

        def finish(db, models, session):
            if check == self.ai_check:
                self.show_ollama(models, turning_on)

        self.app.start_fetch(Fetch("", ollama.list_models, finish))

    def show_ollama(self, models, turning_on):
        if models is None:
            self.show_unreachable(turning_on)
        elif not models and turning_on:
            self.ai_switch.set(False)
            self.set_ai_status(
                "Ollama is running but has no models yet.\n"
                f"1. Click Copy command below. It copies: {PULL_COMMAND}\n"
                f"2. {PASTE_STEP}\n"
                f"3. {FINISH_STEP}",
                [("Copy command", self.copy_pull), ("Try again", self.try_again)],
            )
        else:
            if turning_on:
                ai.save_model(self.db, {"name": models[0]})
            self.show_model(models)

    def show_unreachable(self, turning_on):
        if not turning_on:
            self.set_ai_status(
                "On, but Ollama isn't running. OSSI still works, just without the AI.",
                [("Check again", self.try_again)],
            )
            return
        self.ai_switch.set(False)
        if ollama.is_installed():
            self.set_ai_status(
                "Ollama isn't running. Start it, then try again.",
                [("Try again", self.try_again)],
            )
        else:
            self.set_ai_status(
                "Ollama isn't installed yet.\n"
                "1. Click Download Ollama below and install it.\n"
                f"2. Click Copy command below. It copies: {PULL_COMMAND}\n"
                f"3. {PASTE_STEP}\n"
                f"4. {FINISH_STEP}",
                [
                    ("Download Ollama", lambda: webbrowser.open(OLLAMA_SITE)),
                    ("Copy command", self.copy_pull),
                    ("Try again", self.try_again),
                ],
            )

    def show_model(self, models):
        model = storage.get_setting(self.db, "ai_model")
        if model in models:
            status = f"On, using {model}. OSSI asks before doing anything it suggests."
        else:
            status = f"On, but Ollama no longer has {model}. Pick another model."
        self.set_ai_status(status)
        self.model_box.configure(values=models)
        self.model_box.set(model if model in models else "")
        if len(models) > 1 or model not in models:
            self.model_row.grid()
        else:
            self.model_row.grid_remove()

    def choose_model(self):
        ai.save_model(self.db, {"name": self.model_box.get()})
        self.show_model(list(self.model_box.cget("values")))

    def copy_pull(self):
        self.app.copy(PULL_COMMAND)
        self.ai_status.configure(
            text=f"Copied: {PULL_COMMAND}\n"
            f"Once Ollama is installed: {PASTE_STEP}\n"
            f"{FINISH_STEP}"
        )

    def try_again(self):
        turning_on = not storage.get_setting(self.db, "ai_model")
        self.ai_switch.set(True)
        self.check_ollama(turning_on)

    # Weather

    def build_weather(self):
        self.heading("WEATHER")
        frame = tk.Frame(self.body, bg=BG)
        frame.grid(sticky="ew", padx=SIDE_MARGIN)
        frame.columnconfigure(0, weight=1)
        self.location_label = tk.Label(frame, bg=BG, fg=TEXT, font=FONT, anchor="w")
        self.location_label.grid(row=0, column=0, sticky="w")
        self.note(
            frame,
            "Used for the weather. What you look up is sent to a free weather service.",
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))

        entry_row = tk.Frame(frame, bg=BG)
        entry_row.grid(row=2, column=0, sticky="w", padx=(LABEL_INSET, 0))
        entry_border = tk.Frame(
            entry_row, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER
        )
        entry_border.grid(row=0, column=0)
        self.place_entry = tk.Entry(
            entry_border,
            bg=SURFACE,
            fg=TEXT,
            font=SMALL,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=0,
            width=30,
        )
        self.place_entry.grid(row=0, column=0, padx=8, pady=6)
        self.place_entry.bind("<Return>", lambda event: self.save_place())
        self.place_entry.bind(
            "<FocusIn>", lambda event: entry_border.configure(highlightbackground=ACCENT)
        )
        self.place_entry.bind(
            "<FocusOut>", lambda event: entry_border.configure(highlightbackground=BORDER)
        )
        self.save_image = rounded_image(68, 30, 8, ACCENT, BG)
        self.save_button = tk.Label(
            entry_row,
            text="Save",
            image=self.save_image,
            compound="center",
            bg=BG,
            fg=ON_ACCENT,
            font=(FAMILY, 10, "bold"),
            cursor="hand2",
        )
        self.save_button.grid(row=0, column=1, padx=(10, 0))
        self.save_button.bind("<Button-1>", lambda event: self.save_place())

        self.place_message = self.note(frame)
        self.place_message.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.place_message.grid_remove()
        self.place_links = tk.Frame(frame, bg=BG)
        self.place_links.grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.place_links.grid_remove()
        self.place_choices = tk.Frame(frame, bg=BG)
        self.place_choices.grid(row=5, column=0, sticky="w")
        self.place_choices.grid_remove()

        units = tk.Frame(frame, bg=BG)
        units.grid(row=6, column=0, sticky="w", pady=(8, 2))
        tk.Label(units, text="Temperature", bg=BG, fg=MUTED, font=SMALL).grid(
            row=0, column=0, padx=(0, 10)
        )
        self.unit_buttons = {}
        for column, (unit, text) in enumerate((("fahrenheit", "°F"), ("celsius", "°C")), start=1):
            button = tk.Label(units, text=text, font=SMALL, padx=12, pady=3, cursor="hand2")
            button.grid(row=0, column=column, padx=(0, 2))
            button.bind("<Button-1>", lambda event, chosen=unit: self.choose_unit(chosen))
            self.unit_buttons[unit] = button

    def current_unit(self):
        chosen = weather_skill.preferred_unit(self.db)
        place = weather_skill.saved_location(self.db)
        return chosen or (place["unit"] if place else "fahrenheit")

    def refresh_weather(self):
        place = weather_skill.saved_location(self.db)
        self.location_label.configure(
            text=f"Location: {place['name']}" if place else "Location: not set"
        )
        self.links(self.place_links, [("Forget my location", self.forget_place)] if place else [])
        current = self.current_unit()
        for unit, button in self.unit_buttons.items():
            selected = unit == current
            button.configure(bg=ACCENT if selected else SURFACE, fg=ON_ACCENT if selected else TEXT)

    def say_about_place(self, text):
        self.place_message.configure(text=text)
        self.place_message.grid()

    def save_place(self):
        text = self.place_entry.get().strip()
        if not text:
            return
        self.place_lookup += 1
        lookup = self.place_lookup
        self.clear(self.place_choices)
        self.say_about_place("Looking that up...")

        def finish(db, places, session):
            if lookup == self.place_lookup:
                self.show_places(text, places)

        self.app.start_fetch(Fetch("", lambda: weather.find_places(text), finish))

    def show_places(self, text, places):
        if places is None:
            self.say_about_place(weather_skill.UNREACHABLE)
        elif not places:
            self.say_about_place(weather_skill.not_found(text))
        elif len(places) == 1:
            self.choose_place(places[0])
        else:
            self.say_about_place("Which one?")
            self.clear(self.place_choices)
            self.place_choices.grid()
            for place in places:
                frame = tk.Frame(self.place_choices, bg=BG)
                frame.grid(sticky="w")
                self.links(frame, [(place["name"], lambda chosen=place: self.choose_place(chosen))])

    def choose_place(self, place):
        self.say_about_place(weather_skill.save_location(self.db, place))
        self.clear(self.place_choices)
        self.place_entry.delete(0, "end")
        self.refresh_weather()

    def forget_place(self):
        self.place_lookup += 1
        weather_skill.forget_location(self.db)
        self.clear(self.place_choices)
        self.say_about_place("Forgotten. The weather stays off until you set a new one.")
        self.refresh_weather()

    def choose_unit(self, unit):
        weather_skill.set_unit(self.db, unit)
        self.refresh_weather()

    # Talking

    def build_talk(self):
        self.heading("TALKING")
        frame = tk.Frame(self.body, bg=BG)
        frame.grid(sticky="ew", padx=SIDE_MARGIN)
        self.voice_status = self.note(frame)
        self.voice_status.grid(row=0, column=0, sticky="w", pady=(0, 6))
        choice = tk.Frame(frame, bg=BG)
        choice.grid(row=1, column=0, sticky="w")
        tk.Label(choice, text="Voice", bg=BG, fg=MUTED, font=SMALL).grid(
            row=0, column=0, padx=(0, 10)
        )
        self.voice_box = ttk.Combobox(
            choice, state="readonly", font=SMALL, width=14, values=list(voice.VOICES)
        )
        self.voice_box.grid(row=0, column=1, sticky="w")
        self.voice_box.bind("<<ComboboxSelected>>", lambda event: self.choose_voice())
        self.voice_links = tk.Frame(frame, bg=BG)
        self.voice_links.grid(row=2, column=0, sticky="w", pady=(6, 0))

    def choose_voice(self):
        storage.set_setting(self.db, "talk_voice", self.voice_box.get())
        self.play_sample()

    def play_sample(self):
        self.stop_sample()
        if not voice.is_installed():
            return
        if self.app.talk.active:
            self.voice_status.configure(text="End your conversation to hear a sample.")
            return
        name = self.voice_box.get()
        try:
            self.sample = voice.play_sample(name)
        except OSError:
            self.voice_status.configure(text=f"I couldn't play {name}. See {voice.log_path()}.")
            return
        self.voice_status.configure(text=f"Playing {name}...")
        self.after(SAMPLE_CHECK_MS, self.check_sample, self.sample, name)

    def check_sample(self, process, name):
        if process is not self.sample:
            return  # Another sample replaced it.
        code = process.poll()
        if code is None:
            self.after(SAMPLE_CHECK_MS, self.check_sample, process, name)
            return
        self.sample = None
        if code:
            self.voice_status.configure(text=f"I couldn't play {name}. See {voice.log_path()}.")
        else:
            self.refresh_talk()

    def stop_sample(self):
        if self.sample:
            process, self.sample = self.sample, None
            process.kill()

    def remove_voice(self):
        self.stop_sample()
        self.app.talk.end()
        voice.remove()
        self.refresh_talk()

    def refresh_talk(self):
        chosen = storage.get_setting(self.db, "talk_voice")
        self.voice_box.set(chosen if chosen in voice.VOICES else voice.DEFAULT_VOICE)
        if voice.is_installed():
            self.voice_status.configure(
                text="The voice add-on is installed. Pick a voice to hear it. A new voice applies "
                "to your next conversation."
            )
            self.links(
                self.voice_links,
                [("Hear this voice", self.play_sample), ("Remove the voice add-on", self.remove_voice)],
            )
        else:
            self.voice_status.configure(
                text="Click Talk in the chat to have a spoken conversation. It offers to install "
                "the voice add-on, about 600 MB, the first time. You can hear the voices after that."
            )
            self.links(self.voice_links, [])

    # General

    def build_general(self):
        self.heading("GENERAL")
        self.briefing_switch, _ = self.switch_row(
            "Daily briefing",
            "Shows your day the first time you use OSSI each morning.",
            self.toggle_briefing,
        )
        self.login_switch = None
        if autostart.supported():
            self.login_switch, _ = self.switch_row(
                "Start when I log in",
                "Keeps reminders working after your computer restarts.",
                self.toggle_login,
            )
        self.desktop_status = None
        if desktop.supported():
            frame = tk.Frame(self.body, bg=BG)
            frame.grid(sticky="ew", padx=SIDE_MARGIN, pady=(0, 10))
            tk.Label(frame, text="Desktop shortcut", bg=BG, fg=TEXT, font=FONT).grid(
                row=0, column=0, sticky="w"
            )
            self.desktop_status = self.note(frame)
            self.desktop_status.grid(row=1, column=0, sticky="w")
            self.desktop_links = tk.Frame(frame, bg=BG)
            self.desktop_links.grid(row=2, column=0, sticky="w", pady=(4, 0))

        frame = tk.Frame(self.body, bg=BG)
        frame.grid(sticky="ew", padx=SIDE_MARGIN, pady=(0, 16))
        tk.Label(frame, text="Found a problem?", bg=BG, fg=TEXT, font=FONT).grid(
            row=0, column=0, sticky="w"
        )
        self.note(frame, "Opens GitHub in your browser. You'll need a free GitHub account.").grid(
            row=1, column=0, sticky="w"
        )
        self.report_links = tk.Frame(frame, bg=BG)
        self.report_links.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.links(self.report_links, [("Report a bug", lambda: webbrowser.open(BUG_REPORT_PAGE))])

    def add_to_desktop(self):
        self.desktop_status.configure(text="Adding it...")

        def finish(db, added, session):
            self.refresh_desktop("Added to your Desktop." if added else "That didn't work. Try again?")

        self.app.start_fetch(Fetch("", desktop.create, finish))

    def refresh_desktop(self, message=None):
        if not self.desktop_status:
            return
        there = desktop.exists()
        self.desktop_status.configure(
            text=message
            or (
                "It's on your Desktop. Add it again if its icon looks wrong or OSSI has moved."
                if there
                else "Not on your Desktop. Adding it from here gives it OSSI's icon."
            )
        )
        self.links(
            self.desktop_links,
            [("Add it again" if there else "Add to Desktop", self.add_to_desktop)],
        )

    def toggle_briefing(self, on):
        (briefing.turn_on if on else briefing.turn_off)(self.db)

    def toggle_login(self, on):
        (startup.enable if on else startup.disable)(self.db)

    def refresh_general(self):
        self.briefing_switch.set(storage.get_setting(self.db, "briefing_automatic") != "off")
        if self.login_switch:
            self.login_switch.set(autostart.is_enabled())
        self.refresh_desktop()

    def refresh(self):
        self.refresh_ai()
        self.refresh_talk()
        self.refresh_weather()
        self.refresh_general()
