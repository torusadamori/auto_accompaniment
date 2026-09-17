"""Exact pitch-class matching of physically held keys, including inversions."""
from .chord_progression import Chord, QUALITIES

NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
SUPPORTED = ("", "m", "7", "maj7", "m7")


class HeldNotes:
    def __init__(self):
        self.keys = set()

    def receive(self, message):
        if message.type == "note_on" and message.velocity > 0:
            self.keys.add((message.channel, message.note))
        elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
            self.keys.discard((message.channel, message.note))
        elif message.type == "control_change" and message.control in (120, 123):
            self.keys = {key for key in self.keys if key[0] != message.channel}
        elif message.type == "reset":
            self.keys.clear()

    @property
    def pitch_classes(self):
        return frozenset(note % 12 for _, note in self.keys)


def detect(pitch_classes):
    pcs = frozenset(pitch_classes)
    for root, name in enumerate(NAMES):
        for quality in SUPPORTED:
            chord = Chord(name + quality, root, QUALITIES[quality])
            if frozenset(chord.pitch_classes) == pcs:
                return chord
    return None
