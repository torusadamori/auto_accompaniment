"""Musical events in beats, with no dependency on MIDI ports."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Note:
    beat: float
    duration: float
    pitch: int
    velocity: int
    channel: int
