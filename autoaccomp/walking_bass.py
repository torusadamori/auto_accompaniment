"""Quarter-note bass: chord tones followed by an approach to the next root."""
from .config import BASS_CHANNEL
from .events import Note


def root_note(chord):
    # C2..B2 in MIDI numbering (36..47).
    return 36 + chord.root


def generate(chord, next_chord, bar=0):
    root = root_note(chord)
    target = root_note(next_chord)
    # Prefer a semitone approach with a small distance from the current fifth.
    fifths = [n for n in range(36, 56) if n % 12 == chord.pitch_classes[2]]
    approaches = [n for n in (target - 1, target + 1) if 35 <= n <= 55]
    fifth, approach = min(((f, a) for f in fifths for a in approaches),
                          key=lambda pair: (abs(pair[0] - pair[1]) + abs(pair[0] - root), pair))
    thirds = [n for n in range(36, 56) if n % 12 == chord.pitch_classes[1]]
    third = min(thirds, key=lambda n: (abs(n - root) + abs(n - fifth), n))
    pitches = (root, third, fifth, approach)
    return [Note(float(beat), 0.92, pitch, velocity, BASS_CHANNEL)
            for beat, (pitch, velocity) in enumerate(zip(pitches, (72, 64, 68, 62)))]
