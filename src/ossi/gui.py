"""Desktop window: open tasks in the sidebar, chat with OSSI on the right."""

import ctypes
import queue
import sqlite3
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from ossi import autostart, instance, notifications, router, storage
from ossi.drawing import rounded_image
from ossi.settings_panel import SettingsPanel
from ossi.skills import briefing
from ossi.skills.reminders import fire_due, upcoming
from ossi.skills.tasks import last_completed, open_tasks
from ossi.theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    BORDER,
    FAMILY,
    FONT,
    MUTED,
    ON_ACCENT,
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

SETTINGS_LINK = "⚙  Settings"
BACK_LINK = "←  Back to chat"

BUBBLE_MAX_WIDTH = 420
BOX_HEIGHT = 48
TICK_MS = 500
MISSED_AFTER_SECONDS = 60
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

        root.title("OSSI")
        root.geometry("940x600")
        root.minsize(800, 400)
        root.configure(bg=BG)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        root.report_callback_exception = self.show_error

        if sys.platform == "win32":
            root.iconbitmap(default=str(ICON))
        use_dark_title_bar(root)
        style_widgets(root)
        self.menu = tk.Menu(
            root, tearoff=0, bg=SURFACE, fg=TEXT, activebackground=BORDER, activeforeground=TEXT
        )
        self.menu.add_command(label="Copy")
        self.build_sidebar()
        self.build_chat()
        self.settings = SettingsPanel(root, self)

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
            "due", foreground=ACCENT, font=(FAMILY, 9), spacing3=6, lmargin1=2, lmargin2=2
        )
        self.agenda.grid(row=2, column=0, sticky="nsew", padx=(20, 4), pady=(8, 10))
        add_scrollbar(frame, self.agenda, row=2, column=1, pady=(8, 10), style=SIDEBAR_SCROLLBAR)

        self.undo_label = tk.Label(
            frame, bg=SIDEBAR, fg=ACCENT, font=(FAMILY, 10), cursor="hand2", anchor="w"
        )
        self.undo_label.grid(row=3, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 8))
        self.undo_label.bind("<Button-1>", lambda event: self.run("undo"))
        self.undo_label.bind("<Enter>", lambda event: self.undo_label.configure(fg=ACCENT_HOVER))
        self.undo_label.bind("<Leave>", lambda event: self.undo_label.configure(fg=ACCENT))

        self.settings_label = tk.Label(
            frame, bg=SIDEBAR, fg=MUTED, font=(FAMILY, 10), cursor="hand2", anchor="w"
        )
        self.settings_label.grid(row=4, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 16))
        self.settings_label.bind("<Button-1>", lambda event: self.toggle_settings())
        self.settings_label.bind("<Enter>", lambda event: self.settings_label.configure(fg=TEXT))
        self.settings_label.bind("<Leave>", lambda event: self.settings_label.configure(fg=MUTED))
        self.settings_label.configure(text=SETTINGS_LINK)

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
            "name", foreground=ACCENT, font=SMALL_BOLD, spacing1=12, spacing3=2
        )
        self.log.tag_configure("ossi", spacing3=4, spacing2=3, rmargin=70)
        self.log.tag_configure("you", justify="right", spacing1=16)
        self.log.grid(row=0, column=0, sticky="nsew")
        add_scrollbar(frame, self.log, row=0, column=1)

        # A disabled Text doesn't take focus when clicked, and Ctrl+C needs it.
        self.log.bind("<Button-1>", self.press_log, add=True)
        self.log.bind("<Button-3>", self.show_reply_menu)
        self.send_typing_to_entry(self.log)

        self.box = tk.Canvas(frame, bg=BG, height=BOX_HEIGHT, highlightthickness=0, borderwidth=0)
        self.box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(28, 38), pady=(6, 20))
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
        self.entry.bind("<FocusIn>", lambda event: self.draw_box(ACCENT))
        self.entry.bind("<FocusOut>", lambda event: self.draw_box(BORDER))

        self.send_image = rounded_image(68, 32, 8, ACCENT, SURFACE)
        self.send_hover_image = rounded_image(68, 32, 8, ACCENT_HOVER, SURFACE)
        self.send_button = tk.Label(
            self.box,
            text="Send",
            image=self.send_image,
            compound="center",
            bg=SURFACE,
            fg=ON_ACCENT,
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

        self.box_border = BORDER
        self.box.bind("<Configure>", lambda event: self.draw_box(self.box_border))

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
        self.box.itemconfigure(self.entry_item, width=width - 108)

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
            self.root.destroy()
            return
        self.entry.delete(0, "end")
        self.echo(text)
        self.run(text)

    def run(self, text):
        self.say(router.handle(self.db, text, self.session))
        self.refresh_sidebar()

    def complete(self, task_id):
        self.run(f"done {task_id}")

    def start_fetch(self, fetch):
        """Do slow work on another thread. Only the window's thread touches Tk or the database."""
        threading.Thread(
            target=lambda: self.fetched.put((fetch, fetch.get_value())), daemon=True
        ).start()

    def check_fetches(self):
        while not self.fetched.empty():
            fetch, value = self.fetched.get()
            self.say_result(fetch.finish(self.db, value, self.session))

    def say_result(self, result):
        reply = router.render(self.db, result, self.session)
        if reply is not None:
            self.say(reply, keep_place=True)
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

    def start(self, listener):
        """Begin watching for reminders and launches. Closing the window now hides it."""
        self.listener = listener
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
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
                "Open OSSI again and type exit to close it completely.",
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

        self.agenda.tag_bind(tag, "<Enter>", lambda event: hover(ACCENT, "hand2"))
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
