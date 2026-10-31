"""Listens and speaks during a conversation. Nothing it hears is saved or leaves this computer.

This runs as its own program in the voice add-on's Python environment, because OSSI itself has
no dependencies. It reads commands as lines of JSON on stdin and answers the same way on stdout.
If OSSI goes away, stdin closes and this exits.
"""

import json
import os
import queue
import sys
import threading
import traceback
from collections import deque

RATE = 16000
FRAME = 512  # The speech detector needs exactly this many samples, about 32 ms.
CONTEXT = 64  # And the end of the previous frame.
START_LEVEL = 0.5
START_FRAMES = 3
# Harder to trigger, so that a noise or OSSI's own voice doesn't cut it off.
INTERRUPT_LEVEL = 0.8
INTERRUPT_FRAMES = 10
END_LEVEL = 0.35
END_FRAMES = 20  # About 0.6 seconds. Shorter is quicker, but cuts people off as they pause.
PRE_ROLL_FRAMES = 10  # Keeps the start of a word that came before the detector was sure.
MOST_FRAMES = 940  # About 30 seconds, after which OSSI answers what it has so far.
PLAY_BLOCK = 1200  # About 50 ms, which is how quickly speech can be cut off.
WHISPER_MODEL = "base.en"
NOT_SPEECH = 0.6
UNSURE = -1.0
ECHO_SHARE = 0.8
ECHO_MEMORY = 6  # How many recent sentences to compare against.
LOUD = 0.12  # What counts as full volume for the glow.
HEARD_LEVEL_EVERY = 3  # Frames between sound levels, about 10 a second.
SPOKEN_LEVEL_EVERY = 2  # Likewise for blocks of speech.


class Utterances:
    """Works out from the speech detector's readings when someone starts and stops talking."""

    def __init__(self):
        self.frames = None
        self.quiet = 0
        self.loud = 0
        self.before = deque(maxlen=PRE_ROLL_FRAMES)

    def add(self, frame, level, speaking=False):
        """Returns "started", the finished utterance's frames, or None."""
        if self.frames is None:
            self.before.append(frame)
            enough = level >= (INTERRUPT_LEVEL if speaking else START_LEVEL)
            self.loud = self.loud + 1 if enough else 0
            if self.loud < (INTERRUPT_FRAMES if speaking else START_FRAMES):
                return None
            self.frames = list(self.before)
            self.before.clear()
            self.quiet = self.loud = 0
            return "started"
        self.frames.append(frame)
        self.quiet = self.quiet + 1 if level < END_LEVEL else 0
        if self.quiet < END_FRAMES and len(self.frames) < MOST_FRAMES:
            return None
        finished, self.frames = self.frames, None
        self.quiet = 0
        return finished

    def reset(self):
        self.__init__()


def loudness(samples):
    """How loud some audio is, from 0 to 1, for the glow to follow. `samples` is a numpy array."""
    power = float((samples * samples).mean())
    return round(min((power**0.5 / LOUD) ** 0.5, 1.0), 2)


def words_of(text):
    return "".join(c if c.isalnum() or c.isspace() else " " for c in text.lower()).split()


def is_echo(heard, spoken):
    """Whether the microphone just picked up OSSI's own voice from the speakers."""
    words = words_of(heard)
    said = set(words_of(" ".join(spoken)))
    return bool(words) and sum(word in said for word in words) / len(words) >= ECHO_SHARE


class Worker:
    def __init__(self, protocol, models, voice_name):
        import numpy
        import sounddevice
        from faster_whisper import WhisperModel
        from faster_whisper.vad import get_vad_model
        from kokoro_onnx import Kokoro

        self.protocol = protocol
        self.lock = threading.Lock()
        self.numpy = numpy
        self.sounddevice = sounddevice
        self.voice_name = voice_name
        self.voice = Kokoro(
            os.path.join(models, "kokoro-v1.0.int8.onnx"), os.path.join(models, "voices-v1.0.bin")
        )
        self.whisper = WhisperModel(
            os.path.join(models, "whisper"),
            device="cpu",
            compute_type="int8",
            cpu_threads=min(8, os.cpu_count() or 4),
            local_files_only=True,
        )
        self.detector = get_vad_model().session
        self.memory = (
            numpy.zeros((1, 1, 128), dtype="float32"),
            numpy.zeros((1, 1, 128), dtype="float32"),
            numpy.zeros((1, CONTEXT), dtype="float32"),
        )
        # The first use of each model is much slower, so get it out of the way now.
        _, self.voice_rate = self.voice.create("Hi.", voice=voice_name, lang="en-us")
        list(self.whisper.transcribe(numpy.zeros(RATE, dtype="float32"), language="en")[0])

        self.heard_frames = queue.Queue()
        self.to_make = queue.Queue()
        self.to_play = queue.Queue()
        self.spoken = deque(maxlen=ECHO_MEMORY)
        self.generation = 0  # Sentences from before the latest interruption are dropped.
        self.unfinished = 0  # Sentences waiting to be made or played.
        self.muted = False
        self.interrupted_at_start = False
        self.utterances = Utterances()

    def send(self, **message):
        with self.lock:
            self.protocol.write(json.dumps(message) + "\n")
            self.protocol.flush()

    def start(self):
        for work in (self.listen, self.make_speech, self.play_speech):
            threading.Thread(target=self.guarded, args=(work,), daemon=True).start()
        self.microphone = self.sounddevice.InputStream(
            samplerate=RATE,
            channels=1,
            blocksize=FRAME,
            dtype="float32",
            callback=lambda data, frames, when, status: self.heard_frames.put(data[:, 0].copy()),
        )
        self.microphone.start()
        self.send(type="ready")

    def guarded(self, work):
        try:
            work()
        except Exception as error:
            traceback.print_exc()
            self.send(type="error", message=str(error))

    def level(self, frame):
        h, c, context = self.memory
        joined = self.numpy.concatenate([context, frame[None, :]], axis=1)
        output, h, c = self.detector.run(None, {"input": joined, "h": h, "c": c})
        self.memory = (h, c, frame[None, -CONTEXT:])
        return float(output.reshape(-1)[0])

    def listen(self):
        count = 0
        while True:
            frame = self.heard_frames.get()
            if self.muted:
                self.utterances.reset()
                continue
            speaking = self.unfinished > 0
            count += 1
            if not speaking and count % HEARD_LEVEL_EVERY == 0:
                self.send(type="level", value=loudness(frame))
            result = self.utterances.add(frame, self.level(frame), speaking)
            if result == "started":
                self.interrupted_at_start = speaking
                if speaking:
                    self.stop_speaking()
                    self.send(type="interrupted")
                self.send(type="hearing")
            elif result is not None:
                self.understand(self.numpy.concatenate(result))

    def understand(self, audio):
        segments, _ = self.whisper.transcribe(
            audio, language="en", beam_size=1, condition_on_previous_text=False, vad_filter=False
        )
        text = " ".join(
            segment.text.strip()
            for segment in segments
            if segment.no_speech_prob < NOT_SPEECH and segment.avg_logprob > UNSURE
        ).strip()
        if not text or (self.interrupted_at_start and is_echo(text, self.spoken)):
            self.send(type="nothing")
        else:
            self.send(type="heard", text=text)

    def say(self, text):
        with self.lock:
            self.unfinished += 1
        self.to_make.put((self.generation, text))

    def make_speech(self):
        while True:
            generation, text = self.to_make.get()
            if generation != self.generation:
                continue
            samples, _ = self.voice.create(text, voice=self.voice_name, lang="en-us")
            self.to_play.put((generation, text, samples.astype("float32")))

    def play_speech(self):
        with self.sounddevice.OutputStream(
            samplerate=self.voice_rate, channels=1, dtype="float32"
        ) as speaker:
            while True:
                generation, text, samples = self.to_play.get()
                if generation != self.generation:
                    continue
                self.spoken.append(text)
                self.send(type="speaking")
                for number, start in enumerate(range(0, len(samples), PLAY_BLOCK)):
                    if generation != self.generation:
                        break
                    block = samples[start : start + PLAY_BLOCK]
                    if number % SPOKEN_LEVEL_EVERY == 0:
                        self.send(type="level", value=loudness(block))
                    speaker.write(block)
                with self.lock:
                    if generation != self.generation:
                        continue
                    self.unfinished -= 1
                    finished = self.unfinished == 0
                if finished:
                    self.send(type="quiet")

    def stop_speaking(self):
        with self.lock:
            self.generation += 1
            self.unfinished = 0

    def mute(self, on):
        self.muted = on

    def run_commands(self, commands):
        for line in commands:
            message = json.loads(line)
            if message["type"] == "say":
                self.say(message["text"])
            elif message["type"] == "stop_speaking":
                self.stop_speaking()
            elif message["type"] == "mute":
                self.mute(message["on"])
            elif message["type"] == "quit":
                return


def prepare(models):
    """Download the speech recognition model, the one model not fetched by OSSI itself."""
    from faster_whisper import download_model

    download_model(WHISPER_MODEL, output_dir=os.path.join(models, "whisper"))


def play_sample(models, voice_name, text):
    """Read one line aloud and exit. Doesn't load speech recognition or open the microphone."""
    import sounddevice
    from kokoro_onnx import Kokoro

    voice = Kokoro(
        os.path.join(models, "kokoro-v1.0.int8.onnx"), os.path.join(models, "voices-v1.0.bin")
    )
    samples, rate = voice.create(text, voice=voice_name, lang="en-us")
    sounddevice.play(samples, rate)
    sounddevice.wait()


def main():
    protocol = sys.stdout
    sys.stdout = sys.stderr  # Libraries that print must not disturb the messages.
    if sys.argv[1] == "--prepare":
        prepare(sys.argv[2])
        return
    if sys.argv[1] == "--sample":
        play_sample(*sys.argv[2:5])
        return
    models, voice_name = sys.argv[1:3]
    os.environ["HF_HUB_OFFLINE"] = "1"
    sys.stdin.reconfigure(encoding="utf-8")
    protocol.reconfigure(encoding="utf-8")
    try:
        worker = Worker(protocol, models, voice_name)
        worker.start()
        worker.run_commands(sys.stdin)
    except Exception as error:
        traceback.print_exc()
        protocol.write(json.dumps({"type": "error", "message": str(error)}) + "\n")
        protocol.flush()
    # A thread may be blocked on the sound card, which would keep a normal exit waiting.
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
