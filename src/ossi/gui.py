"""Desktop window: open tasks in the sidebar, chat with OSSI on the right."""

import ctypes
import queue
import sqlite3
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from ossi import (
    autostart,
    desktop,
    instance,
    network,
    notifications,
    ollama,
    router,
    storage,
    theme,
    updater,
)
from ossi.drawing import rounded_image
from ossi.glow import GlowOrb
from ossi.registry import Fetch
from ossi.settings_panel import SettingsPanel
from ossi.skills import briefing
from ossi.skills import network as network_skill
from ossi.skills.reminders import fire_due, upcoming
from ossi.skills.tasks import last_completed, open_tasks
from ossi.talk import Talk
from ossi.theme import (
    BG,
    BORDER,
    FAMILY,
    FONT,
    MUTED,
    SIDEBAR,
    SIDEBAR_SCROLLBAR,
    SMALL_BOLD,
    SURFACE,
    TEXT,
    add_scrollbar,
    style_widgets,
)
from ossi.when import describe

EXIT_WORDS = ("exit", "quit", "bye")
ICON = Path(__file__).parent / "assets" / "ossi.ico"

DESKTOP_OFFER = "Add OSSI to your Desktop?\n\nThe shortcut opens OSSI and shows its icon."
DESKTOP_LATER = "OK. You can add it to your Desktop any time in Settings."
DESKTOP_ADDED = "I added OSSI to your Desktop."
DESKTOP_FAILED = "I couldn't add OSSI to your Desktop. You can try again in Settings."

GOOGLE_HOME_LINK = "⌂  Open Google Home"
GOOGLE_HOME_PAGE = "https://home.google.com/"
QUIT_LINK = "✕  Quit OSSI"
CONFIRM_QUIT = "Quit OSSI completely?\n\nReminders and network alerts stop until you open it again."
SETTINGS_LINK = "⚙  Settings"
BACK_LINK = "←  Back to chat"

WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
SCREEN_MARGIN_WIDTH = 40
SCREEN_MARGIN_HEIGHT = 120  # Room for the title bar and the taskbar.
BUBBLE_MAX_WIDTH = 420
BOX_HEIGHT = 48
TICK_MS = 500
WRITE_MS = 100  # How often a reply being written updates.
FRAMES_PER_DOT = 4
MISSED_AFTER_SECONDS = 60
NETWORK_CHECK_SECONDS = 60
DEVICE_SCAN_SECONDS = 15 * 60
AT_BOTTOM = 0.999  # How far down the chat counts as being at the end, from 0 to 1.

# Tcl crashes if its interpreter is freed off the main thread, and a weather lookup could
# still be running as OSSI quits. Holding the window until Python shuts down prevents that.
open_windows = []


class App:
    def __init__(self, root, db, notify=notifications.send):
        self.root = root
        self.db = db
        self.notify = notify
        self.session = router.Session(defer=self.start_fetch)
        self.fetched = queue.Queue()
        self.listener = None
        self.toasts = []
        self.told_about_background = False
        self.shown_date = None
        self.task_ids = []
        self.reply_count = 0
        self.writing = {}  # The drafts on show, and the number of each one's reply.
        self.write_timer = None
        self.write_frame = 0
        self.network_watch = network_skill.Watch()
        self.network_checking = False
        self.next_network_check = 0.0
        self.next_device_scan = 0.0

        root.title("OSSI")
        root.geometry(starting_size(root.winfo_screenwidth(), root.winfo_screenheight()))
        root.minsize(800, 400)
        root.configure(bg=BG)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        root.report_callback_exception = self.show_error

        if sys.platform == "win32":
            root.iconbitmap(default=str(ICON))
        use_dark_title_bar(root)
        theme.use_accent(storage.get_setting(db, "accent_color"))
        style_widgets(root)
        self.menu = tk.Menu(
            root, tearoff=0, bg=SURFACE, fg=TEXT, activebackground=BORDER, activeforeground=TEXT
        )
        self.menu.add_command(label="Copy")
        self.build_sidebar()
        self.build_chat()
        self.settings = SettingsPanel(root, self)
        self.talk = Talk(self)

        self.say("Hi! Type 'help' to see what I can do.")
        self.refresh_sidebar()
        self.entry.focus_set()

    def build_sidebar(self):
        frame = tk.Frame(self.root, bg=SIDEBAR, width=290)
        frame.grid(row=0, column=0, sticky="ns")
        frame.grid_propagate(False)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        tk.Label(frame, text="OSSI", bg=SIDEBAR, fg=TEXT, font=(FAMILY, 20, "bold")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 0)
        )
        self.date_label = tk.Label(frame, bg=SIDEBAR, fg=MUTED, font=FONT)
        self.date_label.grid(row=1, column=0, sticky="w", padx=20)

        self.agenda = tk.Text(
            frame,
            bg=SIDEBAR,
            fg=TEXT,
            font=FONT,
            wrap="word",
            state="disabled",
            relief="flat",
            highlightthickness=0,
            cursor="arrow",
            padx=0,
            pady=0,
            selectbackground=SIDEBAR,
            selectforeground=TEXT,
            inactiveselectbackground=SIDEBAR,
            exportselection=False,
            width=1,
            height=1,
        )
        self.agenda.tag_configure(
            "heading", foreground=MUTED, font=SMALL_BOLD, spacing1=18, spacing3=4, lmargin1=2
        )
        self.agenda.tag_configure("row", spacing1=6, spacing3=6, lmargin2=28, tabs=(28,))
        self.agenda.tag_configure("id", foreground=MUTED, font=(FAMILY, 9))
        self.agenda.tag_configure("empty", foreground=MUTED, spacing1=6, lmargin1=2, lmargin2=2)
        self.agenda.tag_configure("reminder", spacing1=6, lmargin1=2, lmargin2=2)
        self.agenda.tag_configure(
            "due", foreground=theme.ACCENT, font=(FAMILY, 9), spacing3=6, lmargin1=2, lmargin2=2
        )
        self.agenda.grid(row=2, column=0, sticky="nsew", padx=(20, 4), pady=(8, 10))
        add_scrollbar(frame, self.agenda, row=2, column=1, pady=(8, 10), style=SIDEBAR_SCROLLBAR)

        self.undo_label = tk.Label(
            frame, bg=SIDEBAR, fg=theme.ACCENT, font=(FAMILY, 10), cursor="hand2", anchor="w"
        )
        self.undo_label.grid(row=3, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 8))
        self.undo_label.bind("<Button-1>", lambda event: self.run("undo"))
        self.undo_label.bind("<Enter>", lambda event: self.undo_label.configure(fg=theme.ACCENT_HOVER))
        self.undo_label.bind("<Leave>", lambda event: self.undo_label.configure(fg=theme.ACCENT))

        self.google_home_label = tk.Label(
            frame,
            text=GOOGLE_HOME_LINK,
            bg=SIDEBAR,
            fg=MUTED,
            font=(FAMILY, 10),
            cursor="hand2",
            anchor="w",
        )
        self.google_home_label.grid(row=4, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 8))
        self.google_home_label.bind("<Button-1>", lambda event: webbrowser.open(GOOGLE_HOME_PAGE))
        self.google_home_label.bind("<Enter>", lambda event: self.google_home_label.configure(fg=TEXT))
        self.google_home_label.bind("<Leave>", lambda event: self.google_home_label.configure(fg=MUTED))

        self.settings_label = tk.Label(
            frame, bg=SIDEBAR, fg=MUTED, font=(FAMILY, 10), cursor="hand2", anchor="w"
        )
        self.settings_label.grid(row=5, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 8))
        self.settings_label.bind("<Button-1>", lambda event: self.toggle_settings())
        self.settings_label.bind("<Enter>", lambda event: self.settings_label.configure(fg=TEXT))
        self.settings_label.bind("<Leave>", lambda event: self.settings_label.configure(fg=MUTED))
        self.settings_label.configure(text=SETTINGS_LINK)

        self.quit_label = tk.Label(
            frame,
            text=QUIT_LINK,
            bg=SIDEBAR,
            fg=MUTED,
            font=(FAMILY, 10),
            cursor="hand2",
            anchor="w",
        )
        self.quit_label.grid(row=6, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 16))
        self.quit_label.bind("<Button-1>", lambda event: self.ask_to_quit())
        self.quit_label.bind("<Enter>", lambda event: self.quit_label.configure(fg=TEXT))
        self.quit_label.bind("<Leave>", lambda event: self.quit_label.configure(fg=MUTED))

    def set_accent(self, name):
        """Change the accent color, repainting everything already drawn in the old one."""
        old = (theme.ACCENT, theme.ACCENT_HOVER, theme.ON_ACCENT)
        storage.set_setting(self.db, "accent_color", name)
        theme.use_accent(name)
        changes = dict(zip(old, (theme.ACCENT, theme.ACCENT_HOVER, theme.ON_ACCENT)))
        theme.recolor(self.root, changes)
        self.log.tag_configure("name", foreground=theme.ACCENT)
        self.agenda.tag_configure("due", foreground=theme.ACCENT)
        self.send_image = rounded_image(68, 32, 8, theme.ACCENT, SURFACE)
        self.send_hover_image = rounded_image(68, 32, 8, theme.ACCENT_HOVER, SURFACE)
        self.talk_on_image = rounded_image(68, 32, 8, theme.ACCENT, SURFACE)
        self.send_button.configure(image=self.send_image)
        self.set_talk_button(self.talk.active)
        self.draw_box(changes.get(self.box_border, self.box_border))
        self.settings.restyle()

    def ask_to_quit(self):
        if messagebox.askyesno("OSSI", CONFIRM_QUIT):
            self.quit()

    def quit(self):
        """Stop OSSI completely, unlike closing the window, which keeps it running."""
        self.settings.stop_sample()
        self.talk.end()
        self.root.destroy()

    def toggle_settings(self):
        """Swap the chat for the Settings page, or back again."""
        panel = self.settings
        panel.visible = not panel.visible
        if panel.visible:
            self.chat.grid_remove()
            panel.grid(row=0, column=1, sticky="nsew")
            panel.refresh()
            self.settings_label.configure(text=BACK_LINK)
        else:
            panel.grid_remove()
            self.chat.grid()
            self.settings_label.configure(text=SETTINGS_LINK)
            self.entry.focus_set()
            self.log.see("end")

    def build_chat(self):
        frame = self.chat = tk.Frame(self.root, bg=BG)
        frame.grid(row=0, column=1, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log = tk.Text(
            frame,
            bg=BG,
            fg=TEXT,
            font=FONT,
            wrap="word",
            state="disabled",
            relief="flat",
            highlightthickness=0,
            cursor="xterm",
            padx=28,
            pady=14,
            selectbackground=BORDER,
            selectforeground=TEXT,
            inactiveselectbackground=BORDER,
            width=1,
            height=1,
        )
        self.log.tag_configure(
            "name", foreground=theme.ACCENT, font=SMALL_BOLD, spacing1=12, spacing3=2
        )
        self.log.tag_configure("ossi", spacing3=4, spacing2=3, rmargin=70)
        self.log.tag_configure("you", justify="right", spacing1=16)
        self.log.tag_configure("writing", foreground=MUTED)
        self.log.grid(row=0, column=0, sticky="nsew")
        add_scrollbar(frame, self.log, row=0, column=1)

        # A disabled Text doesn't take focus when clicked, and Ctrl+C needs it.
        self.log.bind("<Button-1>", self.press_log, add=True)
        self.log.bind("<Button-3>", self.show_reply_menu)
        self.send_typing_to_entry(self.log)

        self.build_talk_status(frame)

        self.box = tk.Canvas(frame, bg=BG, height=BOX_HEIGHT, highlightthickness=0, borderwidth=0)
        self.box.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(28, 38), pady=(6, 20))
        self.box_image_item = self.box.create_image(0, 0, anchor="nw")

        self.entry = tk.Entry(
            self.box,
            bg=SURFACE,
            fg=TEXT,
            font=FONT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=0,
        )
        self.entry_item = self.box.create_window(16, BOX_HEIGHT // 2, anchor="w", window=self.entry)
        self.entry.bind("<Return>", lambda event: self.send())
        self.entry.bind("<Control-a>", self.select_all_typed)
        self.entry.bind("<FocusIn>", lambda event: self.draw_box(theme.ACCENT))
        self.entry.bind("<FocusOut>", lambda event: self.draw_box(BORDER))

        self.send_image = rounded_image(68, 32, 8, theme.ACCENT, SURFACE)
        self.send_hover_image = rounded_image(68, 32, 8, theme.ACCENT_HOVER, SURFACE)
        self.send_button = tk.Label(
            self.box,
            text="Send",
            image=self.send_image,
            compound="center",
            bg=SURFACE,
            fg=theme.ON_ACCENT,
            font=(FAMILY, 10, "bold"),
            cursor="hand2",
            borderwidth=0,
            padx=0,
            pady=0,
        )
        self.send_item = self.box.create_window(0, BOX_HEIGHT // 2, anchor="e", window=self.send_button)
        self.send_button.bind("<Button-1>", lambda event: self.send())
        self.send_button.bind(
            "<Enter>", lambda event: self.send_button.configure(image=self.send_hover_image)
        )
        self.send_button.bind(
            "<Leave>", lambda event: self.send_button.configure(image=self.send_image)
        )

        self.talk_image = rounded_image(68, 32, 8, BORDER, SURFACE)
        self.talk_on_image = rounded_image(68, 32, 8, theme.ACCENT, SURFACE)
        self.talk_button = tk.Label(
            self.box,
            text="Talk",
            image=self.talk_image,
            compound="center",
            bg=SURFACE,
            fg=TEXT,
            font=(FAMILY, 10, "bold"),
            cursor="hand2",
            borderwidth=0,
            padx=0,
            pady=0,
        )
        self.talk_item = self.box.create_window(0, BOX_HEIGHT // 2, anchor="e", window=self.talk_button)
        self.talk_button.bind("<Button-1>", lambda event: self.talk.toggle())

        self.box_border = BORDER
        self.box.bind("<Configure>", lambda event: self.draw_box(self.box_border))

    def build_talk_status(self, frame):
        """The strip above the box that shows what a conversation is doing. Hidden until one starts."""
        strip = self.talk_strip = tk.Frame(frame, bg=BG)
        strip.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(28, 38), pady=(6, 0))
        strip.columnconfigure((0, 2), weight=1, uniform="sides")  # Keeps the middle centered.
        self.glow = GlowOrb(strip)
        self.glow.grid(row=0, column=1)
        self.talk_label = tk.Label(strip, bg=BG, fg=theme.ACCENT, font=SMALL_BOLD)
        self.talk_label.grid(row=1, column=1)
        link = self.mute_label = tk.Label(
            strip, text="Mute", bg=BG, fg=MUTED, font=(FAMILY, 10), cursor="hand2"
        )
        link.grid(row=1, column=2, sticky="e")
        link.bind("<Button-1>", lambda event: self.talk.toggle_mute())
        link.bind("<Enter>", lambda event: link.configure(fg=TEXT))
        link.bind("<Leave>", lambda event: link.configure(fg=MUTED))
        strip.grid_remove()

    def show_talk_status(self, text, glow_state=None):
        """Show what a conversation is doing. Without a glow state, it's still being set up."""
        self.talk_label.configure(text=text)
        if glow_state:
            self.mute_label.grid()
            self.glow.grid()
            self.glow.set_state(glow_state)
            self.glow.start()
        else:
            self.mute_label.grid_remove()
            self.glow.grid_remove()
        self.talk_strip.grid()
        self.log.see("end")

    def hide_talk_status(self):
        self.glow.stop()
        self.talk_strip.grid_remove()
        self.set_mute_label(False)

    def set_mute_label(self, muted):
        self.mute_label.configure(text="Unmute" if muted else "Mute")

    def set_talk_button(self, on):
        self.talk_button.configure(
            image=self.talk_on_image if on else self.talk_image,
            fg=theme.ON_ACCENT if on else TEXT,
            text="End" if on else "Talk",
        )

    def select_all_typed(self, event):
        self.entry.selection_range(0, "end")
        self.entry.icursor("end")
        return "break"  # Tk's own Ctrl+A only moves the cursor to the start of the line.

    def draw_box(self, border):
        self.box_border = border
        width = self.box.winfo_width()
        if width <= 1:
            return
        self.box_image = rounded_image(width, BOX_HEIGHT, 12, SURFACE, BG, border)
        self.box.itemconfigure(self.box_image_item, image=self.box_image)
        self.box.coords(self.send_item, width - 8, BOX_HEIGHT // 2)
        self.box.coords(self.talk_item, width - 84, BOX_HEIGHT // 2)
        self.box.itemconfigure(self.entry_item, width=width - 184)

    def say(self, text, keep_place=False):
        """Add a reply. With keep_place, don't pull someone away from reading older messages."""
        follow = not keep_place or self.log.yview()[1] >= AT_BOTTOM
        self.reply_count += 1
        self.log.configure(state="normal")
        self.log.insert(
            "end", "OSSI\n", "name", text, ("ossi", f"reply{self.reply_count}"), "\n", "ossi"
        )
        self.log.configure(state="disabled")
        if follow:
            self.log.see("end")

    def echo(self, text):
        bubble = tk.Canvas(
            self.log,
            bg=BG,
            highlightthickness=0,
            borderwidth=0,
            cursor="xterm",
            selectbackground=BORDER,
            selectforeground=TEXT,
        )
        item = bubble.create_text(
            12, 8, anchor="nw", text=text, fill=TEXT, font=FONT,
            width=BUBBLE_MAX_WIDTH, tags="message",
        )
        x1, y1, x2, y2 = bubble.bbox(item)
        width, height = x2 - x1 + 24, y2 - y1 + 16
        bubble.configure(width=width, height=height)
        bubble.image = rounded_image(width, height, 12, SURFACE, BG)
        bubble.tag_lower(bubble.create_image(0, 0, anchor="nw", image=bubble.image))
        self.make_selectable(bubble, item)

        self.log.configure(state="normal")
        start = self.log.index("end-1c")
        self.log.window_create(start, window=bubble)
        self.log.insert("end-1c", "\n")
        self.log.tag_add("you", start, "end-1c")
        self.log.configure(state="disabled")
        self.log.see("end")

    def make_selectable(self, bubble, item):
        def press(event):
            self.clear_selections()
            bubble.focus_set()
            bubble.select_from(item, f"@{event.x},{event.y}")

        def drag(event):
            bubble.select_to(item, f"@{event.x},{event.y}")

        def menu(event):
            self.show_menu(event, bubble_text(bubble, item))

        bubble.bind("<Button-1>", press)
        bubble.bind("<B1-Motion>", drag)
        bubble.bind("<Button-3>", menu)
        bubble.bind("<<Copy>>", lambda event: self.copy(bubble_text(bubble, item)))
        # The wheel would otherwise stop scrolling while the pointer is over a bubble.
        bubble.bind(
            "<MouseWheel>",
            lambda event: self.log.event_generate("<MouseWheel>", delta=event.delta),
        )
        self.send_typing_to_entry(bubble)

    def press_log(self, event):
        self.clear_selections()
        self.log.focus_set()

    def clear_selections(self):
        self.log.tag_remove("sel", "1.0", "end")
        for bubble in self.log.winfo_children():
            bubble.select_clear()

    def send_typing_to_entry(self, widget):
        """Selecting text moves focus, so keep typing from going nowhere afterward."""

        def forward(event):
            if event.char and event.char.isprintable():
                self.entry.focus_set()
                self.entry.insert("end", event.char)

        widget.bind("<Key>", forward)

    def reply_text(self, x, y):
        """The selected chat text, or else the whole reply at this position."""
        if self.log.tag_ranges("sel"):
            return self.log.get("sel.first", "sel.last")
        for tag in self.log.tag_names(f"@{x},{y}"):
            if tag.startswith("reply"):
                return self.log.get(f"{tag}.first", f"{tag}.last")
        return ""

    def show_reply_menu(self, event):
        self.show_menu(event, self.reply_text(event.x, event.y))

    def show_menu(self, event, text):
        if not text:
            return
        self.menu.entryconfigure(0, command=lambda: self.copy(text))
        self.menu.tk_popup(event.x_root, event.y_root)
        self.menu.grab_release()

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def send(self):
        text = self.entry.get().strip()
        if not text:
            return
        if text.lower() in EXIT_WORDS:
            self.quit()
            return
        self.entry.delete(0, "end")
        self.echo(text)
        self.run(text)

    def run(self, text):
        reply = router.handle(self.db, text, self.session)
        if reply is not None:  # None means a draft is showing instead.
            self.say(reply)
        self.refresh_sidebar()

    def complete(self, task_id):
        self.run(f"done {task_id}")

    def start_fetch(self, fetch):
        """Do slow work on another thread. Only the window's thread touches Tk or the database."""
        if fetch.draft:
            self.start_writing(fetch.draft)
            if self.talk.active:
                self.talk.follow(fetch.draft)
        threading.Thread(
            target=lambda: self.fetched.put((fetch, fetch.get_value())), daemon=True
        ).start()

    def check_fetches(self):
        while not self.fetched.empty():
            fetch, value = self.fetched.get()
            if fetch.draft:
                self.stop_writing(fetch.draft)
            self.say_result(fetch.finish(self.db, value, self.session))

    def start_writing(self, draft):
        """Add a reply that fills in as the draft grows, until the finished one replaces it."""
        self.reply_count += 1
        number = self.writing[draft] = self.reply_count
        self.log.configure(state="normal")
        self.log.insert(
            "end",
            "OSSI\n",
            ("name", f"block{number}"),
            " ",
            ("ossi", f"block{number}", f"body{number}"),
            "\n",
            ("ossi", f"block{number}"),
        )
        self.log.configure(state="disabled")
        self.log.see("end")
        self.draw_draft(draft)
        if self.write_timer is None:
            self.write_timer = self.root.after(WRITE_MS, self.animate_writing)

    def draw_draft(self, draft):
        number = self.writing[draft]
        follow = self.log.yview()[1] >= AT_BOTTOM
        dots = "•" * (self.write_frame // FRAMES_PER_DOT % 3 + 1)
        tags = ("ossi", f"block{number}", f"body{number}")
        self.log.configure(state="normal")
        start = self.log.index(f"body{number}.first")
        self.log.delete(f"body{number}.first", f"body{number}.last")
        self.log.insert(
            start,
            draft.text,
            (*tags, f"reply{number}"),
            f" {dots}" if draft.text else f"Writing a reply {dots}",
            (*tags, "writing"),
        )
        self.log.configure(state="disabled")
        if follow:
            self.log.see("end")

    def animate_writing(self):
        self.write_timer = None
        self.write_frame += 1
        for draft in list(self.writing):
            if draft.stopped():
                self.stop_writing(draft)
            else:
                self.draw_draft(draft)
        if self.writing:
            self.write_timer = self.root.after(WRITE_MS, self.animate_writing)

    def stop_writing(self, draft):
        number = self.writing.pop(draft, None)
        if number is None:
            return  # It was already stopped.
        self.log.configure(state="normal")
        self.log.delete(f"block{number}.first", f"block{number}.last")
        self.log.configure(state="disabled")
        self.log.tag_delete(f"block{number}", f"body{number}", f"reply{number}")

    def say_result(self, result):
        reply = router.render(self.db, result, self.session)
        if reply is not None:
            self.say(reply, keep_place=True)
            if self.talk.active:
                self.talk.reply_finished(reply)
            self.refresh_sidebar()
            if self.settings.visible:
                self.settings.refresh()

    def is_in_use(self):
        # Tk reports no focus window while another app has the focus.
        return self.root.state() == "normal" and bool(self.root.tk.call("focus"))

    def check_briefing(self):
        # While Settings is open the chat is out of sight, so the briefing waits.
        if self.is_in_use() and not self.settings.visible and briefing.is_due(self.db):
            briefing.mark_given(self.db)
            self.say_result(briefing.give_briefing(self.db, self.session, automatic=True))

    def check_network(self):
        """While the network is being watched, look at it now and then without blocking."""
        now = time.time()
        if (
            self.network_checking
            or now < self.next_network_check
            or not network_skill.is_watching(self.db)
        ):
            return
        scan = now >= self.next_device_scan
        self.network_checking = True

        def work():
            status = network.check()
            return status, network.find_devices() if scan and status.connected else None

        def finish(db, result, session):
            self.network_checking = False
            self.next_network_check = time.time() + NETWORK_CHECK_SECONDS
            if result:
                status, devices = result
                if scan and devices is not None:
                    self.next_device_scan = time.time() + DEVICE_SCAN_SECONDS
                self.report_network(status, devices)

        self.start_fetch(Fetch("", work, finish))

    def report_network(self, status, devices):
        alerts = self.network_watch.update(status, time.time())
        for device in network_skill.note_devices(self.db, devices or []):
            alerts.append(("New network device", network_skill.describe_new_device(device)))
        for title, message in alerts:
            self.say(message, keep_place=True)
            self.talk.tell(message)
            self.toast(title, message)

    def start_ollama_if_on(self):
        """Have the local AI ready ahead of the first message, if it's turned on."""
        if storage.get_setting(self.db, "ai_model"):
            threading.Thread(target=ollama.start, daemon=True).start()

    def restart_updated(self):
        """Open the updated OSSI, which replaces this copy within a moment."""
        self.talk.end()
        updater.start_new_copy()

    def check_desktop_offer(self):
        if not self.is_in_use() or not desktop.should_offer(self.db):
            return
        # Timers keep running behind the question, so it's marked as asked before it's shown.
        desktop.mark_offered(self.db)
        if not messagebox.askyesno("OSSI", DESKTOP_OFFER):
            self.say(DESKTOP_LATER, keep_place=True)
            return

        def finish(db, added, session):
            return DESKTOP_ADDED if added else DESKTOP_FAILED

        self.start_fetch(Fetch("", desktop.create, finish))

    def start(self, listener):
        """Begin watching for reminders and launches. Closing the window now hides it."""
        self.listener = listener
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.start_ollama_if_on()
        self.tick()

    def tick(self):
        try:
            if self.listener.poll():
                self.show()
            if self.listener.replaced:
                self.root.destroy()  # Updated code is starting up to take over.
                return
            try:
                self.check_reminders()
                self.check_network()
                self.check_desktop_offer()
                self.check_briefing()
                self.check_fetches()
            except sqlite3.OperationalError:
                pass  # The database is briefly busy, so the next tick tries again.
            self.check_toasts()
            if datetime.now().date() != self.shown_date:
                self.refresh_sidebar()
        finally:
            if not self.listener.replaced:
                self.root.after(TICK_MS, self.tick)

    def hide(self):
        self.root.withdraw()
        if not self.told_about_background:
            self.told_about_background = True
            self.notify(
                "OSSI is still running",
                "It stays in the background for your reminders. "
                "To stop it completely, open OSSI again and click Quit OSSI.",
            )

    def show(self):
        self.refresh_sidebar()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if not self.settings.visible:
            self.entry.focus_set()
        self.log.see("end")

    def check_reminders(self):
        now = time.time()
        fired = fire_due(self.db, now)
        if not fired:
            return
        missed = 0
        for row in fired:
            if now - row["due_at"] > MISSED_AFTER_SECONDS:
                missed += 1
                self.say(
                    f"Missed reminder from {describe(row['due_at'])}: {row['message']}",
                    keep_place=True,
                )
            else:
                self.say(f"Reminder: {row['message']}", keep_place=True)
                self.talk.tell(f"Reminder: {row['message']}")
        if len(fired) > 1:
            self.toast(f"{len(fired)} reminders", "; ".join(row["message"] for row in fired))
        else:
            self.toast("Missed reminder" if missed else "Reminder", fired[0]["message"])
        self.refresh_sidebar()

    def toast(self, title, body):
        process = self.notify(title, body)
        if process is None:
            self.show()
        else:
            self.toasts.append(process)

    def check_toasts(self):
        """If a notification failed, show the window so the reminder isn't lost."""
        for process in list(self.toasts):
            code = process.poll()
            if code is not None:
                self.toasts.remove(process)
                if code != 0:
                    self.show()

    def refresh_sidebar(self):
        now = datetime.now()
        self.shown_date = now.date()
        self.date_label.configure(text=f"{now:%A, %B} {now.day}")

        rows = open_tasks(self.db)
        self.task_ids = [row["id"] for row in rows]
        reminders = upcoming(self.db)
        scrolled_to = self.agenda.yview()[0]

        self.agenda.configure(state="normal", cursor="arrow")
        self.agenda.delete("1.0", "end")
        for tag in self.agenda.tag_names():
            if tag.startswith("check"):
                self.agenda.tag_delete(tag)
        if reminders:
            self.agenda.insert("end", f"REMINDERS  ·  {len(reminders)}\n", "heading")
            for reminder in reminders:
                due = describe(reminder["due_at"], now)
                self.agenda.insert(
                    "end",
                    f"{reminder['message']}\n",
                    "reminder",
                    f"{due[0].upper()}{due[1:]}  ·  #{reminder['id']}\n",
                    "due",
                )
        self.agenda.insert("end", f"TASKS  ·  {len(rows)}\n" if rows else "TASKS\n", "heading")
        for row in rows:
            self.add_task_row(row)
        if not rows:
            self.agenda.insert("end", "All clear. Type 'add task' and a title to add one.", "empty")
        self.agenda.configure(state="disabled")
        self.agenda.yview_moveto(scrolled_to)

        last = last_completed(self.db)
        if last:
            title = last["title"]
            short = f"{title[:27]}..." if len(title) > 30 else title
            self.undo_label.configure(text=f"↩  Undo: {short}")
            self.undo_label.grid()
        else:
            self.undo_label.grid_remove()

    def add_task_row(self, row):
        task_id = row["id"]
        tag = f"check{task_id}"
        self.agenda.tag_configure(tag, foreground=MUTED, font=(FAMILY, 14))

        def hover(color, cursor):
            self.agenda.tag_configure(tag, foreground=color)
            self.agenda.configure(cursor=cursor)

        self.agenda.tag_bind(tag, "<Enter>", lambda event: hover(theme.ACCENT, "hand2"))
        self.agenda.tag_bind(tag, "<Leave>", lambda event: hover(MUTED, "arrow"))
        self.agenda.tag_bind(tag, "<Button-1>", lambda event: self.complete(task_id))
        self.agenda.insert(
            "end",
            "○",
            ("row", tag),
            f"\t{row['title']}  ",
            "row",
            f"#{task_id}",
            ("row", "id"),
            "\n",
            "row",
        )

    def show_error(self, *exc_info):
        # The windowed launcher has no console, so errors would otherwise vanish.
        messagebox.showerror("OSSI", "".join(traceback.format_exception(*exc_info)))


def starting_size(screen_width, screen_height):
    """The window's opening size, smaller if that's what fits on this screen."""
    width = min(WINDOW_WIDTH, screen_width - SCREEN_MARGIN_WIDTH)
    height = min(WINDOW_HEIGHT, screen_height - SCREEN_MARGIN_HEIGHT)
    return f"{width}x{height}"


def bubble_text(bubble, item):
    """The selected part of a bubble, or else all of it."""
    text = bubble.itemcget(item, "text")
    if bubble.select_item():
        return text[bubble.index(item, "sel.first") : bubble.index(item, "sel.last") + 1]
    return text


def use_dark_title_bar(root):
    if sys.platform != "win32":
        return
    root.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    enabled = ctypes.c_int(1)
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(enabled), ctypes.sizeof(enabled)
    )


def main(background=False):
    if instance.signal_running(show=not background):
        return 0
    if sys.platform == "win32":
        # Lets the taskbar show OSSI's icon instead of grouping it under Python.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OSSI")
        # Keeps the login entry correct if the repo moved since it was turned on.
        if autostart.is_enabled():
            autostart.enable()
    db = storage.connect()
    listener = instance.Listener()
    try:
        root = tk.Tk()
        open_windows.append(root)
        if background:
            root.withdraw()
        App(root, db).start(listener)
        root.mainloop()
    finally:
        listener.close()
        db.close()
    return 0
