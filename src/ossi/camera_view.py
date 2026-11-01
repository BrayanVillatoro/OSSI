"""The live view above the message box, which keeps the camera on for as long as it's open."""

import time

from ossi import camera
from ossi.skills import camera as camera_skill

POLL_MS = 33
IDLE_SECONDS = 120
STARTING = "Starting the camera..."
HINT = "Ask about what you see, or press Enter and I'll describe it."
CLOSED_WHEN_IDLE = "I closed the camera, since you hadn't asked anything for a couple of minutes."
COULDNT_OPEN = "I couldn't start the camera add-on. Try removing and adding it again in Settings."


class CameraView:
    def __init__(self, app):
        self.app = app
        self.process = None
        self.shown = None
        self.timer = None
        self.last_used = 0.0

    def toggle(self):
        if self.process:
            self.close()
        elif camera_skill.is_on(self.app.db):
            self.open()
        elif not self.app.settings.visible:
            self.app.toggle_settings()  # That's where it gets turned on.

    def open(self):
        if self.process:
            return
        try:
            self.process = camera.LiveCamera()
        except OSError:
            self.app.say(COULDNT_OPEN)
            return
        self.shown = None
        self.last_used = time.monotonic()
        self.app.session.live_camera = self
        self.app.show_camera_strip(STARTING)
        self.app.set_camera_button(True)
        self.timer = self.app.root.after(POLL_MS, self.poll)

    def poll(self):
        self.timer = None
        latest = self.process.latest
        if self.process.problem:
            self.tell(camera.PROBLEMS[self.process.problem])
        elif time.monotonic() - self.last_used >= IDLE_SECONDS:
            self.tell(CLOSED_WHEN_IDLE)
        else:
            if latest is not self.shown:
                if self.shown is None:
                    self.app.show_camera_strip(HINT)
                self.shown = latest
                self.app.show_camera_view(latest)
            self.timer = self.app.root.after(POLL_MS, self.poll)

    def tell(self, message):
        self.close()
        self.app.say(message, keep_place=True)
        self.app.talk.tell(message)

    def take_photo(self):
        """Take a picture of what's in view. Slow, so it can't run on the window's thread."""
        self.last_used = time.monotonic()
        return self.process.take_photo()

    def close(self):
        if not self.process:
            return
        if self.timer is not None:
            self.app.root.after_cancel(self.timer)
            self.timer = None
        self.process.close()
        self.process = None
        self.app.session.live_camera = None
        self.app.hide_camera_strip()
        self.app.set_camera_button(False)
