"""Uses the webcam and prints what it sees as JSON. Nothing is saved to disk.

This runs as its own program in the camera add-on's Python environment, because OSSI itself has
no dependencies. The camera is only on while this runs. Normally that's a few seconds, to take one
picture. With --live, it keeps sending a small view until told to stop, and takes a picture when
asked, reading commands as lines of JSON on stdin. If OSSI goes away, stdin closes and this exits.
"""

import base64
import json
import os
import sys
import threading
import time

# Windows has two ways to reach a webcam. The first opens quickly, and the second suits some cameras.
BACKENDS = ("CAP_DSHOW", "CAP_MSMF")
CAMERAS_TO_TRY = 3
WIDTH = 1280
HEIGHT = 720
START_SECONDS = 5  # How long a camera gets to send its first frame.
SETTLE_SECONDS = 1  # A webcam's first frames are too dark, until its exposure adjusts.
FAILED_READ_PAUSE = 0.02
TOO_DARK = 8  # The average brightness, from 0 to 255, of a covered lens or a blocked camera.
QUALITY = 90
THUMBNAIL_WIDTH = 280
VIEW_WIDTH = 360
VIEW_FRAMES_PER_SECOND = 15


def read_settled(camera):
    """Keep reading frames until the picture has settled, and return the last. None if none came."""
    started = time.monotonic()
    first_frame_at = None
    frame = None
    while True:
        ok, latest = camera.read()
        now = time.monotonic()
        if ok:
            frame = latest
            if first_frame_at is None:
                first_frame_at = now
        else:
            time.sleep(FAILED_READ_PAUSE)
        if first_frame_at is None and now - started >= START_SECONDS:
            return None
        if first_frame_at is not None and now - first_frame_at >= SETTLE_SECONDS:
            return frame


def grab(cv2, index, backend):
    camera = cv2.VideoCapture(index, backend)
    try:
        if not camera.isOpened():
            return None
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        return read_settled(camera)
    finally:
        camera.release()


def take_picture(cv2):
    """The first good picture from any camera, or else a dark one, or else None."""
    dark = None
    for backend in BACKENDS:
        for index in range(CAMERAS_TO_TRY):
            frame = grab(cv2, index, getattr(cv2, backend))
            if frame is None:
                continue
            if frame.mean() >= TOO_DARK:
                return frame
            dark = frame
    return dark


def encoded(cv2, extension, frame, *options):
    ok, data = cv2.imencode(extension, frame, *options)
    if not ok:
        raise ValueError(f"The picture couldn't be made into a {extension} file")
    return base64.b64encode(data.tobytes()).decode()


def resized(cv2, frame, width):
    """A smaller copy of the frame, or the frame itself if it's already no wider than this."""
    height, current = frame.shape[:2]
    if current <= width:
        return frame
    return cv2.resize(frame, (width, round(height * width / current)), interpolation=cv2.INTER_AREA)


def answer_for(cv2, frame):
    if frame is None:
        return {"error": "no_camera"}
    if frame.mean() < TOO_DARK:
        return {"error": "too_dark"}
    return {
        "picture": encoded(cv2, ".jpg", resized(cv2, frame, WIDTH), [cv2.IMWRITE_JPEG_QUALITY, QUALITY]),
        "thumbnail": encoded(cv2, ".png", resized(cv2, frame, THUMBNAIL_WIDTH)),
    }


def open_first_camera(cv2):
    """The first camera that sends a frame, still open, or None."""
    for backend in BACKENDS:
        for index in range(CAMERAS_TO_TRY):
            camera = cv2.VideoCapture(index, getattr(cv2, backend))
            if camera.isOpened():
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
                started = time.monotonic()
                while time.monotonic() - started < START_SECONDS:
                    if camera.read()[0]:
                        return camera
                    time.sleep(FAILED_READ_PAUSE)
            camera.release()
    return None


def view_of(cv2, frame):
    """A small copy for the window, mirrored like a mirror, because that makes it easy to line things
    up. Tk reads this format the fastest. Pictures taken aren't mirrored, so text in them reads."""
    return encoded(cv2, ".ppm", cv2.flip(resized(cv2, frame, VIEW_WIDTH), 1))


class Commands:
    """What OSSI has asked for, read on a separate thread because reading stdin blocks."""

    def __init__(self, lines):
        self.lines = lines
        self.take = threading.Event()
        self.stop = threading.Event()

    def read(self):
        for line in self.lines:
            if json.loads(line).get("type") == "take":
                self.take.set()
        self.stop.set()


def stream(cv2, camera, commands, send):
    """Send the view and take pictures until told to stop, or until the camera stops working."""
    between_views = 1 / VIEW_FRAMES_PER_SECOND
    next_view_at = 0
    failing_since = None
    while not commands.stop.is_set():
        ok, frame = camera.read()
        now = time.monotonic()
        if not ok:
            failing_since = failing_since or now
            if now - failing_since >= START_SECONDS:
                send({"type": "error", "error": "gone"})
                return
            time.sleep(FAILED_READ_PAUSE)
            continue
        failing_since = None
        if commands.take.is_set():
            commands.take.clear()
            send({"type": "picture", **answer_for(cv2, frame)})
        if now >= next_view_at:
            # Counted from when it was due, so that a frame arriving slightly early doesn't skip one.
            next_view_at = max(next_view_at + between_views, now)
            send({"type": "view", "view": view_of(cv2, frame)})


def run_live(cv2):
    def send(message):
        print(json.dumps(message), flush=True)

    camera = open_first_camera(cv2)
    if camera is None:
        send({"type": "error", "error": "no_camera"})
        return
    commands = Commands(sys.stdin)
    threading.Thread(target=commands.read, daemon=True).start()
    try:
        stream(cv2, camera, commands, send)
    finally:
        camera.release()


def main():
    os.environ["OPENCV_LOG_LEVEL"] = "SILENT"  # It warns about every camera number that's unused.
    import cv2

    if sys.argv[1:] == ["--check"]:  # Only proves that the library loads on this computer.
        return
    if sys.argv[1:] == ["--live"]:
        run_live(cv2)
        sys.stdout.flush()
        os._exit(0)  # A normal exit could wait forever on the thread blocked reading stdin.
    print(json.dumps(answer_for(cv2, take_picture(cv2))))


if __name__ == "__main__":
    main()
