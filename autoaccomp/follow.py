"""Live harmony controller: no fixed progression and no predicted next chord."""
from dataclasses import replace
import math
import time
from . import comping, walking_bass
from .chord_detection import HeldNotes, detect
from .config import BEATS_PER_BAR
from .scheduler import Scheduler


class Follower:
    def __init__(self, output, no_bass=False, no_comping=False, report=print):
        self.notes = HeldNotes()
        self.scheduler = Scheduler(output)
        self.no_bass = no_bass
        self.no_comping = no_comping
        self.report = report
        self.signature = None
        self.settled_signature = None
        self.changed_at = 0.0
        self.label = None
        self.detected = None
        self.current = None
        self.last_beat = -1
        self.chord_start = 0
        self.previous = None

    def tick(self, beat, now):
        signature = self.notes.pitch_classes
        if signature != self.signature:
            self.signature = signature
            self.changed_at = now
        # Collect slightly staggered chord key presses without delaying MIDI thru.
        if signature != self.settled_signature and now - self.changed_at >= 0.04:
            self.settled_signature = signature
            self.detected = detect(signature)
            label = self.detected.symbol if self.detected else ("Unknown" if signature else "(no notes)")
            if label != self.label:
                self.report(f"Detected chord: {label}")
                self.label = label

        boundary = int(beat)
        if boundary != self.last_beat:
            self.last_beat = boundary
            if self.detected != self.current:
                self.scheduler.clear()
                self.current = self.detected
                self.chord_start = boundary
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
                self.scheduler.add(events, boundary)
        self.scheduler.tick(beat)


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
