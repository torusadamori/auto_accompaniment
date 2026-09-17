"""Adapt single-note history and a harmony estimator to existing accompaniment."""
import time
from .follow import Follower
from .melody_history import MelodyHistory
from .harmony_estimator import HarmonyEstimator
from .progression_estimator import ProgressionEstimator
from .jazz_harmony import JazzHarmony
from .jazz_style import JazzAccompaniment


class MelodyFollower:
    def __init__(self, output, tempo=120, key="C", no_bass=False, no_comping=False,
                 report=print, debug_harmony=False, debug_accomp=False, start=None, progression_aware=False,
                 style="basic", seed=1):
        if style not in ("basic", "jazz"):
            raise ValueError("Style must be basic or jazz.")
        self.style = style
        self.history = MelodyHistory()
        self.progression_aware = progression_aware or style == "jazz"
        self.estimator = JazzHarmony(key) if style == "jazz" else ProgressionEstimator(key) if progression_aware else HarmonyEstimator(key)
        self.engine = (JazzAccompaniment(output, no_bass, no_comping, report, debug_accomp, seed)
                       if style == "jazz" else Follower(output, no_bass, no_comping, report, debug_accomp))
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
            if self.debug_harmony and (not self.progression_aware or self.estimator.evaluated):
                self.report(f"Melody notes: {[note.note for note in notes]}")
                ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
                if self.progression_aware:
                    self.report(f"Current chord: {previous.symbol if previous else '(none)'}")
                    self.report("Candidate scores:")
                    for name, score in ranking:
                        part = self.estimator.parts[name]
                        self.report(f"{name:2} melody={part.melody:.2f} transition={part.transition:.2f} "
                                    f"hold={part.hold:.2f} bar={part.bar:.2f} total={score:.2f}")
                else:
                    self.report("Candidates: " + ", ".join(f"{name}={score:.2f}" for name, score in ranking))
            if chord is not None and chord != previous:
                self.report(f"Estimated chord: {chord.symbol}")
            self.engine.detected = chord
            if self.style == "jazz":
                pending = self.estimator.pending if self.estimator.pending_beat == boundary+1 else None
                self.engine.context(notes, pending)
                if self.debug_harmony and self.estimator.pending is not None:
                    self.report(f"Pending chord: {self.estimator.pending.symbol} at beat {self.estimator.pending_beat}")
        self.engine.tick_accompaniment(beat)
