"""Gesture-driven MIDI shaping, independent of harmony estimation/accompaniment.

Times are seconds relative to the transport start; beats are quarter notes.
Phase 1 uses authored data, not a transcription of Loopian's implementation.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
import heapq
import math
import time

import mido

from .midi_io import input_port, output_port


PALETTES = {
    "Cmaj7": (0, 4, 7, 11, 2, 9),
    "Dm7": (2, 5, 9, 0, 4, 7),
    "G7": (7, 11, 2, 5, 9, 4),
}
DEFAULT_CHORDS = ("Cmaj7", "Dm7", "G7", "Cmaj7")


@dataclass(frozen=True)
class MelodyNote:
    beat: float
    duration: float
    note: int


@dataclass(frozen=True)
class MusicalContext:
    tempo: float
    key: str
    bar: int  # zero-based absolute bar, including repeats
    beat: float  # position within bar
    chord: str
    next_chord: str
    melody: tuple
    melody_range: tuple


@dataclass(frozen=True)
class FixedSong:
    """Replaceable context source: a future MIDI loader can build authored data here."""
    tempo: float = 120
    chords: tuple = DEFAULT_CHORDS
    beats_per_bar: int = 4
    melody: tuple = (
        MelodyNote(0, 2, 64), MelodyNote(2, 2, 67),
        MelodyNote(4, 2, 65), MelodyNote(6, 2, 69),
        MelodyNote(8, 2, 67), MelodyNote(10, 2, 65),
        MelodyNote(12, 2, 64), MelodyNote(14, 2, 60),
    )
    output_range: tuple = (55, 84)

    def __post_init__(self):
        if not math.isfinite(self.tempo) or not 20 <= self.tempo <= 300:
            raise ValueError("Tempo must be between 20 and 300 BPM.")
        if not self.chords or any(c not in PALETTES for c in self.chords):
            raise ValueError("Phase 1 chords: Cmaj7, Dm7, G7.")
        if self.beats_per_bar != 4:
            raise ValueError("Phase 1 supports 4/4 only.")
        low, high = self.output_range
        if not 0 <= low < high <= 127 or high - low < 12:
            raise ValueError("Note range must be within 0..127 and span at least an octave.")

    def at(self, seconds):
        beat = max(0, seconds) * self.tempo / 60
        bar = int(beat // self.beats_per_bar)
        return MusicalContext(self.tempo, "C major", bar, beat % self.beats_per_bar,
                              self.chords[bar % len(self.chords)],
                              self.chords[(bar + 1) % len(self.chords)], self.melody,
                              (min(n.note for n in self.melody), max(n.note for n in self.melody))
                              if self.melody else self.output_range)


@dataclass(frozen=True)
class Gesture:
    note: int
    interval: int
    direction: str
    elapsed: float | None  # inter-onset interval; rhythm/speed input for later phases


class GestureAnalyzer:
    def __init__(self):
        self.previous = None
        self.time = None

    def receive(self, note, now):
        interval = 0 if self.previous is None else note - self.previous
        direction = ("START" if self.previous is None else
                     "UP" if interval > 0 else "DOWN" if interval < 0 else "SAME")
        result = Gesture(note, interval, direction, None if self.time is None else now - self.time)
        self.previous, self.time = note, now
        return result


class PitchShaper:
    def __init__(self, low=55, high=84):
        self.low, self.high = low, high
        self.previous = None

    def choose(self, gesture, context):
        allowed = PALETTES[context.chord]
        candidates = [n for n in range(self.low, self.high + 1) if n % 12 in allowed]
        if self.previous is None:
            # Begin near the authored melody register, independent of the input key.
            anchor = sum(context.melody_range) / 2
            chord_tones = [n for n in candidates if n % 12 in allowed[:4]]
            output = min(chord_tones or candidates, key=lambda n: (abs(n - anchor), n))
        elif gesture.direction in ("UP", "DOWN"):
            sign = 1 if gesture.direction == "UP" else -1
            directional = [n for n in candidates if (n - self.previous) * sign > 0]
            if directional:
                output = min(directional, key=lambda n: abs(n - self.previous))
            else:
                # Next valid pitch outside the range, folded by whole octaves.
                output = self.previous + sign
                while output % 12 not in allowed:
                    output += sign
                while output > self.high:
                    output -= 12
                while output < self.low:
                    output += 12
        else:
            output = min(candidates, key=lambda n: (abs(n - self.previous), n))
        self.previous = output
        return output


@dataclass(frozen=True)
class Timing:
    grid: int = 16  # 0 disables correction
    strength: float = 0.5
    window: float = 0.030

    def __post_init__(self):
        if self.grid not in (0, 8, 16):
            raise ValueError("Grid must be 0, 8 or 16.")
        if not math.isfinite(self.strength) or not 0 <= self.strength <= 1:
            raise ValueError("Timing strength must be between 0 and 1.")
        if not math.isfinite(self.window) or not 0 <= self.window <= 0.1:
            raise ValueError("Timing window must be between 0 and 100 ms.")

    def due(self, now, tempo):
        if not self.grid:
            return now
        spacing = 60 / tempo * 4 / self.grid
        nearest = math.floor(now / spacing + 0.5) * spacing
        delta = nearest - now
        # Causal correction: already-past beats cannot be reached without lookahead.
        return now + delta * self.strength if 0 < delta <= self.window else now


@dataclass
class Voice:
    gesture: Gesture
    velocity: int
    received: float
    due: float
    note: int | None = None


def note_name(note):
    return "None" if note is None else f"{('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')[note % 12]}{note // 12 - 1}"


class LoopianEngine:
    """FIFO input ownership plus an ordered, bounded MIDI output queue."""
    LIMIT = 4096

    def __init__(self, target, song, timing=Timing(), report=None):
        self.target, self.song, self.timing, self.report = target, song, timing, report
        self.gestures = GestureAnalyzer()
        self.pitch = PitchShaper(*song.output_range)
        self.held = defaultdict(deque)
        self.sounding = {}
        self.queue = []
        self.sequence = 0
        self.last_due = 0

    def schedule(self, due, kind, voice, velocity=0):
        if len(self.queue) >= self.LIMIT:
            raise RuntimeError("Loopian event queue full; stopping and releasing notes.")
        self.sequence += 1
        heapq.heappush(self.queue, (due, self.sequence, kind, voice, velocity))

    def receive(self, message, now):
        if message.type == "control_change":
            if message.control in (120, 123):
                self.close()
            elif message.control == 64:
                self.target.send(message.copy(channel=0, time=0))
            return
        if message.type not in ("note_on", "note_off"):
            return  # No pitch bend/raw MIDI thru that could bypass the palette.
        key = (message.channel, message.note)
        if message.type == "note_on" and message.velocity:
            if sum(len(v) for v in self.held.values()) >= self.LIMIT:
                raise RuntimeError("Loopian held-note limit reached; stopping and releasing notes.")
            due = max(self.last_due, self.timing.due(now, self.song.tempo))
            self.last_due = due  # Do not reorder a rapid gesture around a grid edge.
            voice = Voice(self.gestures.receive(message.note, now), message.velocity, now, due)
            self.held[key].append(voice)
            self.schedule(due, "on", voice)
        elif key in self.held:
            voice = self.held[key].popleft()
            if not self.held[key]:
                del self.held[key]
            # Shift release by the same amount as attack, including short taps.
            self.schedule(max(voice.due + 0.001, now + (voice.due - voice.received)),
                          "off", voice, message.velocity)

    def tick(self, now):
        while self.queue and self.queue[0][0] <= now:
            due, _, kind, voice, velocity = heapq.heappop(self.queue)
            if kind == "off":
                if voice.note is not None and self.sounding.get(voice.note) is voice:
                    self.target.send(mido.Message("note_off", note=voice.note, velocity=velocity))
                    del self.sounding[voice.note]
                continue
            context = self.song.at(now)  # Harmony at actual output, even across a bar boundary.
            previous = self.pitch.previous
            note = self.pitch.choose(voice.gesture, context)
            if note in self.sounding:
                self.target.send(mido.Message("note_off", note=note))
            self.target.send(mido.Message("note_on", note=note, velocity=voice.velocity))
            voice.note = note
            self.sounding[note] = voice
            if self.report:
                g = voice.gesture
                self.report(f"Input note: {g.note}\nInterval: {g.interval:+d}\nDirection: {g.direction}\n"
                            f"Chord: {context.chord}; Next chord: {context.next_chord}; "
                            f"Bar: {context.bar + 1}; Beat: {context.beat + 1:.3f}\n"
                            f"Previous output: {note_name(previous)}\nOutput note: {note} ({note_name(note)})\n"
                            f"Timing: input={voice.received:.4f}s scheduled={due:.4f}s actual={now:.4f}s")

    def close(self):
        self.queue.clear()
        self.held.clear()
        self.target.send(mido.Message("control_change", control=64, value=0))
        for note in list(self.sounding):
            self.target.send(mido.Message("note_off", note=note))
            del self.sounding[note]
        self.gestures = GestureAnalyzer()
        self.pitch.previous = None
        self.last_due = 0


def run_loopian(args):
    song = FixedSong(tempo=args.tempo, chords=tuple(args.chords),
                     output_range=(args.note_min, args.note_max))
    timing = Timing(args.grid, args.timing_strength, args.timing_window_ms / 1000)
    if args.bars < 0:
        raise ValueError("Bars must be >= 0 (0 means continuous).")
    with output_port(args.output) as target, input_port(args.input) as source:
        target.send(mido.Message("program_change", program=0))
        engine = LoopianEngine(target, song, timing,
                               (lambda line: print(line, flush=True)) if args.debug_loopian else None)
        print(f"Loopian ready: C major, 4/4, {song.tempo:g} BPM; input = gesture. Ctrl+C stops.", flush=True)
        start = time.perf_counter()
        last_bar = -1
        try:
            while True:
                now = time.perf_counter() - start
                context = song.at(now)
                if args.bars and context.bar >= args.bars:
                    break
                if context.bar != last_bar:
                    print(f"Bar {context.bar + 1}: {context.chord} -> {context.next_chord}", flush=True)
                    last_bar = context.bar
                engine.tick(now)
                for _ in range(256):
                    message = source.poll()
                    if message is None:
                        break
                    now = time.perf_counter() - start
                    engine.receive(message, now)
                    engine.tick(now)
                time.sleep(0.001)
        finally:
            engine.close()
