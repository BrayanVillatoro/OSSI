"""The glowing light that shows what a spoken conversation is doing, and reacts to the voices."""

import math
import time
import tkinter as tk

from ossi.theme import ACCENT, BG, MUTED

SIZE = 96
FRAME_MS = 33  # About 30 frames a second.
STEPS = 24  # How many different sizes of glow get drawn. Each is only drawn once.
SHADES = 128
RISE = 0.35  # How quickly it follows a voice getting louder, from 0 to 1.
FALL = 0.12  # Slower, so that it fades instead of flickering between words.
LEVEL_FADE = 0.85  # Each frame, in case the sound levels stop arriving.

WAITING = "waiting"
LISTENING = "listening"
HEARING = "hearing"
THINKING = "thinking"
SPEAKING = "speaking"
MUTED_STATE = "muted"

COLORS = {
    WAITING: MUTED,
    LISTENING: ACCENT,
    HEARING: "#7DF0CE",
    THINKING: "#8F9BFF",
    SPEAKING: "#5FD4F4",
    MUTED_STATE: "#4A4F5C",
}


def target_strength(state, level, now):
    """How strongly to glow, from 0 to 1. `level` is how loud the voice is, also from 0 to 1."""
    if state == MUTED_STATE:
        return 0.05
    if state in (WAITING, THINKING):
        return 0.4 + 0.2 * math.sin(now * 4)
    if state == SPEAKING:
        return 0.25 + 0.75 * level
    if state == HEARING:
        return 0.35 + 0.65 * level
    return 0.22 + 0.06 * math.sin(now * 1.6) + 0.7 * level  # Breathing slowly while it listens.


def next_strength(current, target):
    return current + (target - current) * (RISE if target > current else FALL)


def glow_rows(size, color, background, strength):
    """The picture of one glow, as rows of colors: a soft disc fading out into the background."""
    under = [int(background[i : i + 2], 16) for i in (1, 3, 5)]
    over = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    palette = [
        "#{:02X}{:02X}{:02X}".format(
            *(round(u + (o - u) * shade / (SHADES - 1)) for u, o in zip(under, over))
        )
        for shade in range(SHADES)
    ]
    middle = (size - 1) / 2
    edge = size / 2
    core = size * (0.12 + 0.10 * strength)
    reach = size * (0.16 + 0.20 * strength)
    brightness = 0.25 + 0.65 * strength
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            distance = math.hypot(x - middle, y - middle)
            if distance < core:
                amount = 1.0
            else:
                outward = (distance - core) / reach
                amount = brightness * math.exp(-3 * outward * outward)
                amount *= max(1 - (distance / edge) ** 4, 0)  # Never cut off at the canvas's edge.
                amount = max(amount, min(max(core + 1 - distance, 0), 1))  # Smooths the disc's rim.
            row.append(palette[round(min(amount, 1.0) * (SHADES - 1))])
        rows.append(row)
    return rows


class GlowOrb(tk.Canvas):
    def __init__(self, parent):
        super().__init__(
            parent, width=SIZE, height=SIZE, bg=BG, highlightthickness=0, borderwidth=0
        )
        self.frames = {}
        self.item = self.create_image(0, 0, anchor="nw")
        self.state_name = WAITING
        self.level = 0.0
        self.strength = 0.0
        self.timer = None

    def frame(self, color, step):
        key = (color, step)
        if key not in self.frames:
            rows = glow_rows(SIZE, color, BG, step / (STEPS - 1))
            image = tk.PhotoImage(master=self, width=SIZE, height=SIZE)
            image.put(" ".join("{" + " ".join(row) + "}" for row in rows))
            self.frames[key] = image
        return self.frames[key]

    def set_state(self, state):
        self.state_name = state

    def set_level(self, level):
        self.level = max(self.level, level)

    def start(self):
        if self.timer is None:
            self.animate()

    def stop(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.level = self.strength = 0.0

    def animate(self):
        target = target_strength(self.state_name, self.level, time.monotonic())
        self.strength = next_strength(self.strength, target)
        self.level *= LEVEL_FADE
        # Nothing to draw while Settings covers it or the window is closed.
        if self.winfo_viewable():
            step = round(min(max(self.strength, 0), 1) * (STEPS - 1))
            self.itemconfigure(self.item, image=self.frame(COLORS[self.state_name], step))
        self.timer = self.after(FRAME_MS, self.animate)
