"""Adapt single-note history and a harmony estimator to existing accompaniment."""
import time
from .follow import Follower
from .melody_history import MelodyHistory
from .harmony_estimator import HarmonyEstimator


class MelodyFollower:
    def __init__(self, output, tempo=120, key="C", no_bass=False, no_comping=False,
                 report=print, debug_harmony=False, debug_accomp=False, start=None):
        self.history = MelodyHistory()
        self.estimator = HarmonyEstimator(key)
        self.engine = Follower(output, no_bass, no_comping, report, debug_accomp)
        self.scheduler = self.engine.scheduler
        self.tempo = tempo
        self.start = time.perf_counter() if start is None else start
        self.report = report
        self.debug_harmony = debug_harmony
        self.last_boundary = -1

    def receive(self, message, now=None):
        now = time.perf_counter() if now is None else now
        beat = (now - self.start) * self.tempo / 60
        self.history.receive(message, now, beat)

    def tick(self, beat, now):
        boundary = int(beat)
        if boundary != self.last_boundary:
            self.last_boundary = boundary
            notes = self.history.recent(boundary)
            previous = self.estimator.current
            chord, scores = self.estimator.estimate(notes, boundary, self.history.window_beats)
            if self.debug_harmony:
                self.report(f"Melody notes: {[note.note for note in notes]}")
                ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
                self.report("Candidates: " + ", ".join(f"{name}={score:.2f}" for name, score in ranking))
            if chord is not None and chord != previous:
                self.report(f"Estimated chord: {chord.symbol}")
            self.engine.detected = chord
        self.engine.tick_accompaniment(beat)
