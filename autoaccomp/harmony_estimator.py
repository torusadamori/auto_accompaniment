"""Small deterministic C-major scoring model; replaceable independently of output."""
from .chord_progression import parse_chord

CANDIDATES = tuple(parse_chord(name) for name in ("C", "Dm", "Em", "F", "G", "Am"))
SCALE = frozenset((0, 2, 4, 5, 7, 9, 11))
TRANSITIONS = frozenset((("C", "F"), ("C", "G"), ("Dm", "G"), ("G", "C"), ("Am", "Dm")))


class HarmonyEstimator:
    def __init__(self, key="C", candidates=None):
        if key != "C":
            raise ValueError("Third MVP supports only --key C (C major).")
        self.candidates = tuple(candidates) if candidates is not None else CANDIDATES
        if not self.candidates:
            raise ValueError("At least one candidate chord is required.")
        self.current = None
        self.last_change = float("-inf")
        self.min_hold_beats = 2
        self.change_margin = 0.35

    def score(self, notes, beat, window_beats=4):
        weighted = []
        for note in notes:
            end = min(beat, note.end_beat if note.end_beat is not None else beat)
            start = max(note.onset_beat, beat - window_beats)
            if end < start or note.onset_beat > beat:
                continue
            duration = max(0.1, end - start)
            phase = note.onset_beat % 4
            # A 0.2-beat tolerance on either side of beats 1 and 3.
            strong = min(phase, abs(phase - 2), 4 - phase) <= 0.2
            recency = max(0.5, 1 - (beat - end) / (2 * window_beats))
            weighted.append((note.pitch_class, duration * (1.5 if strong else 1) * recency))
        total = sum(weight for _, weight in weighted)
        scores = {}
        for chord in self.candidates:
            raw = sum(weight * (3 if pc in chord.pitch_classes else 0.5 if pc in SCALE else -1)
                      for pc, weight in weighted)
            scores[chord.symbol] = raw / total if total else 0.0
            if self.current is not None and total:
                if chord == self.current:
                    scores[chord.symbol] += 0.20
                elif (self.current.symbol, chord.symbol) in TRANSITIONS:
                    scores[chord.symbol] += 0.15
        return scores

    def estimate(self, notes, beat, window_beats=4):
        scores = self.score(notes, beat, window_beats)
        # No evidence (or only out-of-key notes): retain harmony, never cycle by rules alone.
        if not notes or not any(note.pitch_class in SCALE for note in notes):
            return self.current, scores
        best = max(self.candidates, key=lambda chord: scores[chord.symbol])
        if self.current is None or (
            beat - self.last_change >= self.min_hold_beats
            and scores[best.symbol] > scores[self.current.symbol] + self.change_margin
        ):
            if best != self.current:
                self.current = best
                self.last_change = beat
        return self.current, scores
