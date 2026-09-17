"""Small explicit chord vocabulary, independent of MIDI I/O."""
from dataclasses import dataclass
import re
from .config import PROGRESSION

QUALITIES = {"": (0, 4, 7), "m": (0, 3, 7), "maj7": (0, 4, 7, 11), "7": (0, 4, 7, 10),
             "m7": (0, 3, 7, 10), "m7b5": (0, 3, 6, 10)}
ROOTS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


@dataclass(frozen=True)
class Chord:
    symbol: str
    root: int
    intervals: tuple[int, ...]

    @property
    def pitch_classes(self):
        return tuple((self.root + interval) % 12 for interval in self.intervals)


def parse_chord(symbol):
    match = re.fullmatch(r"([A-G])([#b]?)(maj7|m7b5|m7|7|m|)", symbol)
    if not match:
        raise ValueError(f"Unsupported chord {symbol!r}; use C, Am, Cmaj7, A7, Dm7, Bm7b5, etc.")
    letter, accidental, quality = match.groups()
    root = (ROOTS[letter] + {"": 0, "#": 1, "b": -1}[accidental]) % 12
    return Chord(symbol, root, QUALITIES[quality])


def progression(symbols=PROGRESSION):
    result = tuple(parse_chord(symbol) for symbol in symbols)
    if not result:
        raise ValueError("Progression must contain at least one chord.")
    return result
