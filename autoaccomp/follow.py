"""Live harmony controller: no fixed progression and no predicted next chord."""
from dataclasses import replace
import math
import time
from . import comping, walking_bass
from .chord_detection import HeldNotes, detect
from .config import BEATS_PER_BAR, COMP_CHANNEL, BASS_CHANNEL
from .events import Note
from .scheduler import Scheduler


class DebugOutput:
    """Observe successfully sent attacks, never planned or dropped events."""
    def __init__(self, output, report):
        self.output = output
        self.report = report
        self.sent = []

    def send(self, message):
        self.output.send(message)
        if message.type == "note_on" and message.velocity > 0:
            self.sent.append((message.channel, message.note))

    def flush(self, chord, beat):
        sent, self.sent = self.sent, []
        if not sent:
            return
        self.report(f"Accompaniment output: {chord.symbol} (beat {beat:.2f})")
        comp = [pitch for channel, pitch in sent if channel == COMP_CHANNEL]
        bass = [pitch for channel, pitch in sent if channel == BASS_CHANNEL]
        if comp:
            self.report(f"Comping notes: {comp}")
        for pitch in bass:
            self.report(f"Bass note: {pitch}")


class Follower:
    def __init__(self, output, no_bass=False, no_comping=False, report=print, debug_accomp=False):
        self.notes = HeldNotes()
        self.debug_output = DebugOutput(output, report) if debug_accomp else None
        self.scheduler = Scheduler(self.debug_output or output)
        self.no_bass = no_bass
        self.no_comping = no_comping
        self.report = report
        self.collection_seconds = 0.08
        self.collection_deadline = None
        self.collected = set()
        self.held_display = None
        self.next_held_report = 0.0
        self.label = None
        self.detected = None
        self.current = None
        self.last_beat = -1
        self.chord_start = 0
        self.previous = None

    def receive(self, message, now=None):
        """Collect attacks for 80ms from the first attack, retaining short notes."""
        now = time.perf_counter() if now is None else now
        self.finish_collection(now)
        self.notes.receive(message)
        if message.type == "note_on" and message.velocity > 0:
            if self.collection_deadline is None:
                self.collection_deadline = now + self.collection_seconds
                self.collected = {note for _, note in self.notes.keys}
            self.collected.add(message.note)

    def finish_collection(self, now):
        if self.collection_deadline is None or now < self.collection_deadline:
            return
        pcs = {note % 12 for note in self.collected}
        result = detect(pcs)
        # During legato changes old keys may overlap the collection window.
        if result is None:
            result = detect(self.notes.pitch_classes)
        label = result.symbol if result else "Unknown"
        if label != self.label:
            self.report(f"Collected notes: {sorted(self.collected)}")
            self.report(f"Detected chord: {label}")
            self.label = label
        if result is not None:
            self.detected = result  # Last VALID chord; releases/Unknown never erase it.
        self.collection_deadline = None
        self.collected.clear()

    def tick(self, beat, now):
        self.finish_collection(now)
        held = sorted({note for _, note in self.notes.keys})
        if held != self.held_display and now >= self.next_held_report:
            self.report(f"Held notes: {held}")
            self.held_display = held
            self.next_held_report = now + 0.08

        boundary = int(beat)
        if boundary != self.last_beat:
            self.last_beat = boundary
            if self.detected != self.current:
                self.scheduler.clear()
                self.current = self.detected
                self.chord_start = boundary
                self.report(f"Active accompaniment chord: {self.current.symbol}")
            if self.current is not None:
                age = boundary - self.chord_start
                phase = age % BEATS_PER_BAR
                events = []
                if not self.no_comping:
                    pattern, voices = comping.generate(self.current, age // BEATS_PER_BAR, self.previous)
                    events.extend(replace(note, beat=note.beat - phase)
                                  for note in pattern if phase <= note.beat < phase + 1)
                    if events:
                        self.previous = voices
                if not self.no_bass:
                    # Future harmony is unknown: approach the current root until it changes.
                    bass = walking_bass.generate(self.current, self.current)[phase]
                    events.append(replace(bass, beat=0))
                if self.debug_output is not None and age == 0:
                    # A clear root-position chord only on the change beat.
                    # Subsequent beats keep the existing jazz comping and bass.
                    events = []
                    if not self.no_comping:
                        voices = tuple(60 + self.current.root + interval
                                       for interval in self.current.intervals)
                        events.extend(Note(0, 0.70, pitch, 78, COMP_CHANNEL) for pitch in voices)
                        self.previous = voices
                    if not self.no_bass:
                        events.append(Note(0, 0.92, walking_bass.root_note(self.current), 84, BASS_CHANNEL))
                self.scheduler.add(events, boundary)
        try:
            self.scheduler.tick(beat)
        finally:
            if self.debug_output is not None:
                self.debug_output.flush(self.current, beat)


def run_follow(follower, tempo, bars, service):
    if not math.isfinite(tempo) or not 20 <= tempo <= 300:
        raise ValueError("Tempo must be between 20 and 300 BPM.")
    if bars < 0:
        raise ValueError("Bars must be >= 0 (0 means continuous).")
    start = time.perf_counter()
    try:
        while True:
            service()
            now = time.perf_counter()
            beat = (now - start) * tempo / 60
            if bars and beat >= bars * BEATS_PER_BAR:
                return
            follower.tick(beat, now)
            time.sleep(0.001)
    finally:
        follower.scheduler.clear()
