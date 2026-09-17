"""Opt-in progression-aware scoring; the third-MVP estimator stays unchanged."""
from dataclasses import dataclass
from .harmony_estimator import HarmonyEstimator, SCALE

TRANSITION_SCORES = {
    "C": {"F": 0.30, "G": 0.30, "Am": 0.25},
    "Dm": {"G": 0.45, "Am": 0.25},
    "Em": {"Am": 0.30, "F": 0.30},
    "F": {"G": 0.45, "C": 0.30, "Dm": 0.25},
    "G": {"C": 0.45, "Am": 0.25},
    "Am": {"Dm": 0.45, "F": 0.30, "G": 0.30},
}


@dataclass(frozen=True)
class CandidateScore:
    melody: float
    transition: float = 0.0
    hold: float = 0.0
    bar: float = 0.0

    @property
    def total(self):
        return self.melody + self.transition + self.hold + self.bar


class ProgressionEstimator(HarmonyEstimator):
    def __init__(self, key="C", candidates=None):
        super().__init__(key, candidates)
        self.parts = {}
        self.evaluated = False
        self.evaluated_through = float("-inf")
        self.last_scores = {chord.symbol: 0.0 for chord in self.candidates}

    def score_parts(self, notes, beat, window_beats=4):
        weighted = []
        for note in notes:
            end = min(beat, note.end_beat if note.end_beat is not None else beat)
            start = max(note.onset_beat, beat - window_beats)
            if end < start or note.onset_beat > beat:
                continue
            duration = max(0.05, end - start)
            phase = note.onset_beat % 4
            accent = 2.0 if min(phase, 4-phase) <= 0.2 else 1.5 if abs(phase-2) <= 0.2 else 1.0
            recency = max(0.5, 1 - (beat-end) / (2*window_beats))
            weighted.append((note.pitch_class, duration ** 1.25 * accent * recency))
        total = sum(weight for _, weight in weighted)
        result = {}
        for chord in self.candidates:
            raw = sum(weight * (3 if pc in chord.pitch_classes else 0.5 if pc in SCALE else -1)
                      for pc, weight in weighted)
            melody = raw / total if total else 0.0
            transition = hold = bar = 0.0
            if self.current is not None and total:
                same = chord == self.current
                transition = 0 if same else TRANSITION_SCORES.get(self.current.symbol, {}).get(chord.symbol, -0.15)
                hold = 0.25 if same else 0.0
                bar = (0.0 if same else 0.10) if beat % 4 == 0 else (0.20 if same else 0.0)
            result[chord.symbol] = CandidateScore(melody, transition, hold, bar)
        return result

    def score(self, notes, beat, window_beats=4):
        return {name: part.total for name, part in self.score_parts(notes, beat, window_beats).items()}

    def estimate(self, notes, beat, window_beats=4):
        self.evaluated = False
        eligible = tuple(note for note in notes if note.onset_beat <= beat and
                         (note.end_beat is None or note.end_beat > beat-window_beats))
        # A newly ended short note is processed once at the next boundary. During
        # subsequent rest beats, neither aging evidence nor bar bonuses cause changes.
        fresh = any(note.onset_beat > self.evaluated_through or note.end_beat is None or
                    note.end_beat > self.evaluated_through for note in eligible)
        if not eligible or not fresh:
            return self.current, self.last_scores.copy()
        self.evaluated = True
        self.evaluated_through = beat
        self.parts = self.score_parts(eligible, beat, window_beats)
        self.last_scores = {name: part.total for name, part in self.parts.items()}
        if not any(note.pitch_class in SCALE for note in eligible):
            return self.current, self.last_scores.copy()
        best = max(self.candidates, key=lambda chord: self.last_scores[chord.symbol])
        best_melody = max(part.melody for part in self.parts.values())
        # Progression can break close musical ties, but cannot override poor melody fit.
        fits_melody = self.parts[best.symbol].melody >= best_melody - 0.4
        if self.current is None or (fits_melody and
            beat - self.last_change >= self.min_hold_beats and
            self.last_scores[best.symbol] > self.last_scores[self.current.symbol] + self.change_margin
        ):
            if best != self.current:
                self.current = best
                self.last_change = beat
        return self.current, self.last_scores.copy()
