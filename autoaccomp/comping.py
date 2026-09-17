"""Quiet rootless three-note voicings with minimal voice movement."""
from itertools import product
from .config import COMP_CHANNEL
from .events import Note
from .chord_progression import Chord

# Two deterministic bars: space for the melody, a little offbeat syncopation.
PATTERNS = (((0.0, 0.65, 53), (2.5, 0.40, 46)),
            ((1.0, 0.55, 50), (3.0, 0.45, 44)))


def voicing(chord, previous=None):
    if len(chord.intervals) == 3:
        # Simple jazz coloring: major -> major seventh, minor -> minor seventh.
        seventh = 11 if chord.intervals[1] == 4 else 10
        chord = Chord(chord.symbol, chord.root, chord.intervals + (seventh,))
    pcs = chord.pitch_classes[1:]  # third, fifth, seventh
    choices = [[note for note in range(52, 73) if note % 12 == pc] for pc in pcs]
    candidates = {tuple(sorted(notes)) for notes in product(*choices)
                  if max(notes) - min(notes) <= 12}
    anchor = previous or (55, 60, 64)
    return min(candidates, key=lambda notes: (sum(abs(a-b) for a, b in zip(notes, anchor)), notes))


def generate(chord, bar, previous=None):
    notes = voicing(chord, previous)
    events = [Note(beat, duration, pitch, velocity, COMP_CHANNEL)
              for beat, duration, velocity in PATTERNS[bar % len(PATTERNS)]
              for pitch in notes]
    return events, notes
