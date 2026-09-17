"""Bounded melody history in seconds and beats, independent of MIDI ports."""
from collections import deque
from dataclasses import dataclass


@dataclass
class MelodyNote:
    note: int
    velocity: int
    channel: int
    onset_time: float
    onset_beat: float
    end_time: float | None = None
    end_beat: float | None = None

    @property
    def pitch_class(self):
        return self.note % 12

    @property
    def duration(self):
        return None if self.end_time is None else self.end_time - self.onset_time


class MelodyHistory:
    def __init__(self, window_beats=4.0, max_notes=256):
        self.window_beats = window_beats
        self.notes = deque(maxlen=max_notes)
        self.active = None

    def release(self, now, beat):
        if self.active is not None:
            self.active.end_time = now
            self.active.end_beat = beat
            self.active = None

    def receive(self, message, now, beat):
        if message.type == "note_on" and message.velocity > 0:
            # Monophonic analysis: a new attack ends the previous analysis note.
            # MIDI thru is independent, so actual playback is never truncated here.
            self.release(now, beat)
            self.active = MelodyNote(message.note, message.velocity, message.channel, now, beat)
            self.notes.append(self.active)
        elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
            if self.active and (message.channel, message.note) == (self.active.channel, self.active.note):
                self.release(now, beat)
        elif message.type == "control_change" and message.control in (120, 123):
            if self.active and message.channel == self.active.channel:
                self.release(now, beat)
        elif message.type == "reset":
            self.release(now, beat)

    def recent(self, beat):
        cutoff = beat - self.window_beats
        while self.notes and self.notes[0].end_beat is not None and self.notes[0].end_beat <= cutoff:
            self.notes.popleft()
        return tuple(note for note in self.notes if note.onset_beat <= beat)
