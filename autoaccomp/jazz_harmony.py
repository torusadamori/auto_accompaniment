"""Bar-oriented decisions on top of the existing progression-aware scores."""
import math
from .progression_estimator import ProgressionEstimator


class JazzHarmony(ProgressionEstimator):
    def __init__(self, key="C"):
        super().__init__(key)
        self.pending = None
        self.pending_beat = None

    def estimate(self, notes, beat, window_beats=4):
        if self.pending is not None and beat >= self.pending_beat:
            self.current, self.last_change = self.pending, beat
            self.pending = self.pending_beat = None
        old, changed = self.current, self.last_change
        proposed, scores = super().estimate(notes, beat, window_beats)
        if old is None:
            return proposed, scores
        self.current, self.last_change = old, changed
        if self.evaluated:
            if proposed == old:
                self.pending = self.pending_beat = None
            else:
                margin = scores[proposed.symbol] - scores[old.symbol]
                melody_gain = self.parts[proposed.symbol].melody - self.parts[old.symbol].melody
                # Only a strong musical improvement permits a half-bar change.
                urgent = beat % 4 == 2 and beat-changed >= 2 and margin >= 0.9 and melody_gain >= 0.5
                if urgent:
                    self.current, self.last_change = proposed, beat
                    self.pending = self.pending_beat = None
                    return self.current, scores
                self.pending = proposed
                self.pending_beat = max(math.ceil(beat/4)*4, math.ceil((changed+4)/4)*4)
        # A previously selected change may execute during a rest: this is a
        # scheduled decision, not a new inference from aging evidence.
        if self.pending is not None and beat >= self.pending_beat:
            self.current, self.last_change = self.pending, beat
            self.pending = self.pending_beat = None
        return self.current, scores
