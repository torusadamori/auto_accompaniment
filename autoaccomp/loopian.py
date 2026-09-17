"""Gesture-driven MIDI shaping, independent of harmony estimation/accompaniment.

Times are seconds relative to the transport start; beats are quarter notes.
Phase 1 uses authored data, not a transcription of Loopian's implementation.
"""
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from functools import cached_property
import heapq
import math
import time
import statistics

import mido

from .midi_io import input_port, output_port
from .midi_melody import MidiMelody, load_melody
from .loopian_flow import FlowSelector
from .midi_analysis import analyze_midi, chord_palette, parse_chord, meter_position, strong_beats, format_analysis


PALETTES = {
    "Cmaj7": (0, 4, 7, 11, 2, 9),
    "Dm7": (2, 5, 9, 0, 4, 7),
    "G7": (7, 11, 2, 5, 9, 4),
}
DEFAULT_CHORDS = ("Cmaj7", "Dm7", "G7", "Cmaj7")
PRIMARY = {chord: notes[:4] for chord, notes in PALETTES.items()}
SECONDARY = {chord: notes[4:] for chord, notes in PALETTES.items()}


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
    primary: tuple = ()
    secondary: tuple = ()
    accents: tuple = (0, 2)
    harmony_confidence: float = 1.0
    harmony_source: str = "authored"

    @property
    def chord_tones(self):
        return self.primary if self.primary or self.secondary else PRIMARY[self.chord]

    @property
    def allowed(self):
        return self.primary + self.secondary if self.primary or self.secondary else PALETTES[self.chord]


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
    output_range: tuple = (48, 96)

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
class MidiSong:
    """Harmony follows the human-stepped source position, not wall-clock time."""
    source: MidiMelody
    chords: tuple | None = None
    tempo_override: float | None = None
    output_range: tuple = (48, 96)
    adapt_register: bool = False

    def __post_init__(self):
        FixedSong(tempo=120 if self.tempo_override is None else self.tempo_override,
                  chords=self.chords if self.chords is not None and self.source.analysis is None else DEFAULT_CHORDS,
                  output_range=self.output_range)
        if self.source.analysis is not None and self.chords:
            for chord in self.chords:
                parse_chord(chord)
        if self.source.analysis is None and self.chords is None and any(c not in PALETTES for _, c in self.source.chord_markers):
            raise ValueError("MIDI Chord: markers must be Cmaj7, Dm7 or G7; use --chords to override.")

    @property
    def tempo(self):
        return self.tempo_override if self.tempo_override is not None else self.source.tempo_at(0)

    @cached_property
    def register_shift(self):
        if not self.adapt_register:
            return 0
        pitches = [n.note for n in self.source.notes]
        desired = round(((sum(self.output_range) / 2) - statistics.median(pitches)) / 12)
        octaves = min((127 - max(pitches)) // 12, max(math.ceil(-min(pitches) / 12), desired))
        return 12 * octaves

    @cached_property
    def melody(self):
        return tuple(replace(n, note=n.note + self.register_shift) for n in self.source.notes) if self.register_shift else self.source.notes

    @cached_property
    def melody_range(self):
        return min(n.note for n in self.melody), max(n.note for n in self.melody)

    def context_for(self, position):
        cycle, index = divmod(position, len(self.melody))
        beat = self.melody[index].beat
        if self.source.analysis is not None:
            analysis = self.source.analysis
            bar, within, meter = meter_position(analysis.meters, beat)
            harmony = analysis.harmony_at(beat, self.chords)
            following = analysis.harmony_at(self.melody[(index + 1) % len(self.melody)].beat, self.chords)
            primary, secondary = chord_palette(harmony.chord) if harmony.chord else ((), analysis.key.scale)
            return MusicalContext(self.tempo_override if self.tempo_override is not None else self.source.tempo_at(beat),
                                  analysis.key.name, cycle * analysis.bars + bar, within,
                                  harmony.chord or f"{analysis.key.name} scale",
                                  following.chord or f"{analysis.key.name} scale", self.melody, self.melody_range,
                                  primary, secondary, strong_beats(meter.numerator, meter.denominator),
                                  harmony.confidence, harmony.source)
        local_bar = int(beat // 4)
        chords = self.chords if self.chords is not None else DEFAULT_CHORDS
        chord = chords[local_bar % len(chords)]
        next_chord = chords[(local_bar + 1) % len(chords)]
        markers = self.source.chord_markers if self.chords is None else ()
        for onset, name in markers:
            if onset <= beat:
                chord = name
            else:
                next_chord = name
                break
        else:
            if markers:
                next_chord = markers[0][1] if markers[0][0] == 0 else chords[0]
        tempo = self.tempo_override if self.tempo_override is not None else self.source.tempo_at(beat)
        return MusicalContext(tempo, "C major", cycle * max(1, math.ceil(self.source.length_beats / 4)) + local_bar,
                              beat % 4, chord, next_chord, self.melody, self.melody_range)


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
    def __init__(self, low=48, high=96, phrase_gap=0.7, tone_priority="chord"):
        if tone_priority not in ("flat", "chord"):
            raise ValueError("Tone priority must be flat or chord.")
        self.tone_priority = tone_priority
        self.last_chord = None
        self.change_notes = 0
        self.selection_reason = ""
        self.material = None
        if not math.isfinite(phrase_gap) or phrase_gap <= 0:
            raise ValueError("Phrase gap must be finite and greater than zero.")
        self.phrase_gap = phrase_gap
        self.low, self.high = low, high
        self.previous = None
        self.boundary_reason = None
        self.rebase_zone = None

    def phrase_note(self, candidates, allowed, anchor, tones=None):
        tones = allowed[:4] if tones is None else tones
        primary = [n for n in candidates if n % 12 in tones]
        # Register/distance first; prefer 3rd, 7th, root, 5th on ties.
        identity = tuple(tones[i] for i in (1, 3, 0, 2) if i < len(tones))
        return min(primary or candidates,
                   key=lambda n: (abs(n - anchor), identity.index(n % 12)
                                  if n % 12 in identity else 4, n))

    def scored_note(self, candidates, allowed, strong, tones=None):
        tones = allowed[:4] if tones is None else tones
        nearest = min(abs(n - self.previous) for n in candidates)
        # Never buy chord colour with a leap larger than a fourth, unless
        # even the nearest valid note is farther away (e.g. a custom range).
        nearby = [n for n in candidates if abs(n - self.previous) <= max(5, nearest)]
        penalty = 4 if strong else 1.5
        if self.material is not None:
            # Mirror the source interval magnitude when the human reverses it.
            # Octave/register drift is controlled by the same Phase 1 leap cap.
            sign = 1 if nearby[0] > self.previous else -1
            target = self.previous + sign * max(1, min(5, abs(self.material.interval)))
            return min(nearby, key=lambda n: (
                abs(n - target)
                + 0.25 * min((n - self.material.note) % 12, (self.material.note - n) % 12)
                + 0.15 * abs(n - self.previous)
                + (penalty if self.tone_priority == "chord" and n % 12 not in tones else 0),
                n % 12 not in tones, abs(n - self.previous), n))
        return min(nearby, key=lambda n: (
            abs(n - self.previous) + (0 if n % 12 in tones else penalty),
            n % 12 not in tones, abs(n - self.previous), n))

    def rebase(self, candidates, allowed, direction, tones=None):
        tones = allowed[:4] if tones is None else tones
        if direction == "SAME":
            self.rebase_zone = "PREVIOUS"
            return min(candidates, key=lambda n: (abs(n - self.previous), n))
        middle = (self.low + self.high) / 2
        width = (self.high - self.low) / 4
        zone_low, zone_high = ((middle - width, middle) if direction == "UP"
                               else (middle, middle + width))
        self.rebase_zone = "LOW_MID" if direction == "UP" else "HIGH_MID"
        interior = candidates[1:-1] or candidates
        zone = [n for n in interior if zone_low <= n <= zone_high] or interior
        chord_tones = [n for n in zone if n % 12 in tones]
        center = (zone_low + zone_high) / 2
        if self.tone_priority == "chord":
            return self.phrase_note(zone, allowed, center, tones)
        return min(chord_tones or zone, key=lambda n: (abs(n - center), n))

    def choose(self, gesture, context, range_restart=False, material=None):
        self.material = material if gesture.direction in ("UP", "DOWN") else None
        self.boundary_reason = None
        self.rebase_zone = None
        allowed, tones = context.allowed, context.chord_tones
        changed = self.last_chord is not None and self.last_chord != context.chord
        if changed:
            self.change_notes = 2
        # Zero-based beats 0 and 2: first sixteenth-note window of beats 1/3.
        strong_beat = any(0 <= context.beat - beat < 0.25 for beat in context.accents)
        strong = strong_beat or self.change_notes > 0
        self.selection_reason = ("flat nearest" if self.tone_priority == "flat" else
                                 "PRIMARY weighted + direction/distance; " +
                                 ("chord change" if self.change_notes else
                                  "strong beat" if strong_beat else "weak beat"))
        candidates = [n for n in range(self.low, self.high + 1) if n % 12 in allowed]
        if range_restart:
            self.boundary_reason = "RANGE_LIMIT"
            output = self.rebase(candidates, allowed, gesture.direction, tones)
        elif self.previous is None:
            # Begin near the authored melody register, independent of the input key.
            anchor = material.note if material is not None else sum(context.melody_range) / 2
            chord_tones = [n for n in candidates if n % 12 in tones]
            output = min(chord_tones or candidates, key=lambda n: (abs(n - anchor), n))
            if self.tone_priority == "chord":
                output = self.phrase_note(candidates, allowed, anchor, tones)
        elif gesture.elapsed is not None and gesture.elapsed >= self.phrase_gap:
            self.boundary_reason = "INPUT_PAUSE"
            output = self.rebase(candidates, allowed, gesture.direction, tones)
        elif gesture.direction in ("UP", "DOWN"):
            sign = 1 if gesture.direction == "UP" else -1
            directional = [n for n in candidates if (n - self.previous) * sign > 0]
            if directional:
                output = min(directional, key=lambda n: abs(n - self.previous))
                if self.tone_priority == "chord" or material is not None:
                    output = self.scored_note(directional, allowed, strong, tones)
            else:
                self.boundary_reason = "RANGE_LIMIT"
                output = self.rebase(candidates, allowed, gesture.direction, tones)
        else:
            self.material = None  # SAME preserves pitch; never applies a source interval.
            output = min(candidates, key=lambda n: (abs(n - self.previous), n))
            if self.tone_priority == "chord" and self.change_notes:
                output = self.scored_note(candidates, allowed, True, tones)
            else:
                self.selection_reason = "SAME: preserve/nearest allowed"
        if self.tone_priority == "chord" and (self.previous is None or self.boundary_reason):
            self.selection_reason = "phrase PRIMARY + nearest register (3rd/7th/root/5th tie-break)"
            if gesture.direction == "SAME" and self.previous is not None:
                if self.change_notes:
                    output = self.scored_note(candidates, allowed, True, tones)
                self.selection_reason = "SAME: preserve/nearest allowed; chord-change weighting only"
        self.last_chord = context.chord
        self.change_notes = max(0, self.change_notes - 1)
        if material is not None:
            if self.selection_reason == "flat nearest":
                self.selection_reason = "flat melody contour + source pitch class + distance"
            self.selection_reason += f"; input {gesture.direction} + melody {material.contour} ({material.interval:+d})"
        if not tones:
            self.selection_reason = "key-scale fallback; " + self.selection_reason
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
    released: float | None = None
    boundary_started: bool = False
    melody_position: int | None = None
    attack_order: int | None = None


def note_name(note):
    return "None" if note is None else f"{('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')[note % 12]}{note // 12 - 1}"


class LoopianEngine:
    """FIFO input ownership plus an ordered, bounded MIDI output queue."""
    LIMIT = 4096
    PHRASE_GAP = 0.060

    def __init__(self, target, song, timing=Timing(), report=None, phrase_gap=0.7, tone_priority="chord",
                 mode="gesture", flow_window=3, flow_strength=0.5, seed=None, source_accent=0):
        if not math.isfinite(source_accent) or not 0 <= source_accent <= 1:
            raise ValueError("Source accent must be finite and between 0 and 1.")
        self.source_accent = source_accent
        if mode not in ("gesture", "melody-direct", "melody-transform", "melody-flow"):
            raise ValueError("Unknown Loopian mode.")
        if mode != "gesture" and not isinstance(song, MidiSong):
            raise ValueError("Melody modes require --midi-file.")
        if mode == "gesture" and isinstance(song, MidiSong):
            raise ValueError("MIDI material requires a melody mode.")
        self.mode = mode
        self.flow = FlowSelector(flow_window, flow_strength, seed) if mode == "melody-flow" else None
        # Zero strength takes the unchanged Phase 2 path, including queue reservations.
        self.flow_active = self.flow is not None and self.flow.amount > 0
        self.melody_position = 0
        self.target, self.song, self.timing, self.report = target, song, timing, report
        self.gestures = GestureAnalyzer()
        self.pitch = PitchShaper(*song.output_range, phrase_gap=phrase_gap, tone_priority=tone_priority)
        self.held = defaultdict(deque)
        self.sounding = {}
        self.queue = []
        self.sequence = 0
        self.last_due = 0
        self.resume_at = 0
        self.sustain = 0
        self.restore_sustain = False

    def schedule(self, due, kind, voice, velocity=0):
        if len(self.queue) >= self.LIMIT:
            raise RuntimeError("Loopian event queue full; stopping and releasing notes.")
        self.sequence += 1
        if kind == "on" and voice.attack_order is None:
            voice.attack_order = self.sequence
        order = voice.attack_order if kind == "on" else self.sequence
        heapq.heappush(self.queue, (due, order, self.sequence, kind, voice, velocity))

    def receive(self, message, now):
        if message.type == "control_change":
            if message.control in (120, 123):
                self.close()
            elif message.control == 64:
                self.sustain = message.value
                if not self.restore_sustain:
                    self.target.send(message.copy(channel=0, time=0))
            return
        if message.type not in ("note_on", "note_off"):
            return  # No pitch bend/raw MIDI thru that could bypass the palette.
        key = (message.channel, message.note)
        if message.type == "note_on" and message.velocity:
            if sum(len(v) for v in self.held.values()) >= self.LIMIT:
                raise RuntimeError("Loopian held-note limit reached; stopping and releasing notes.")
            position = self.melody_position if self.mode != "gesture" else None
            tempo = self.song.context_for(position).tempo if position is not None else self.song.tempo
            if self.flow_active:
                position = None  # Resolve FLOW position in output order, after earlier choices.
            due = max(self.last_due, self.resume_at, self.timing.due(now, tempo))
            self.last_due = due  # Do not reorder a rapid gesture around a grid edge.
            voice = Voice(self.gestures.receive(message.note, now), message.velocity, now, due,
                          melody_position=position)
            self.held[key].append(voice)
            self.schedule(due, "on", voice)
            if position is not None:
                self.melody_position += 1
        elif key in self.held:
            voice = self.held[key].popleft()
            if not self.held[key]:
                del self.held[key]
            voice.released = now
            # Shift release by the same amount as attack, including short taps.
            self.schedule(max(voice.due + 0.001, now + (voice.due - voice.received)),
                          "off", voice, message.velocity)

    def tick(self, now):
        while self.queue and self.queue[0][0] <= now:
            due, _, _, kind, voice, velocity = heapq.heappop(self.queue)
            if kind == "off":
                # A range boundary may have deferred an already queued short tap.
                release_due = max(voice.due + 0.001,
                                  voice.released + (voice.due - voice.received))
                if release_due > now:
                    self.schedule(release_due, "off", voice, velocity)
                    continue
                if voice.note is not None and self.sounding.get(voice.note) is voice:
                    self.target.send(mido.Message("note_off", note=voice.note, velocity=velocity))
                    del self.sounding[voice.note]
                continue
            if due < self.resume_at:
                # Even if tick arrives after the gap, older pending attacks must
                # move behind the boundary's first attack, in received order.
                voice.due = self.resume_at
                self.schedule(voice.due, "on", voice)
                continue
            material, selection = None, None
            previous = self.pitch.previous
            harmony_state = self.pitch.last_chord, self.pitch.change_notes
            if self.flow_active:
                selection = self.flow.select(self.pitch, self.song, voice.gesture, self.melody_position,
                                             PALETTES, range_restart=voice.boundary_started)
                context = selection.context
                material = self.song.melody[selection.selected.position % len(self.song.melody)]
            elif voice.melody_position is not None:
                context = self.song.context_for(voice.melody_position)
                material = self.song.melody[voice.melody_position % len(self.song.melody)]
            else:
                context = self.song.at(now)  # Phase 1 harmony follows actual output time.
            if selection is not None:
                self.pitch = selection.pitch
                note = selection.selected.note
            elif self.mode == "melody-direct":
                note = material.note
                self.pitch.previous = note
                self.pitch.boundary_reason = None
                self.pitch.selection_reason = "melody-direct: original pitch; human timing/velocity/release"
            else:
                note = self.pitch.choose(voice.gesture, context, range_restart=voice.boundary_started,
                                         material=material)
            reason = self.pitch.boundary_reason
            if reason == "RANGE_LIMIT" and not voice.boundary_started:
                # End the old phrase audibly, not merely by relabeling an octave jump.
                self.target.send(mido.Message("control_change", control=64, value=0))
                for sounding in list(self.sounding):
                    self.target.send(mido.Message("note_off", note=sounding))
                    del self.sounding[sounding]
                self.restore_sustain = True
                self.pitch.previous = previous
                self.pitch.last_chord, self.pitch.change_notes = harmony_state
                voice.boundary_started = True
                voice.due = now + self.PHRASE_GAP
                self.resume_at = voice.due
                self.schedule(voice.due, "on", voice)
                if self.report:
                    edge = "ceiling" if voice.gesture.direction == "UP" else "floor"
                    self.report(f"Range {edge} reached\nPhrase boundary detected\n"
                                f"Reason: RANGE_LIMIT\nPhrase gap: {self.PHRASE_GAP * 1000:g}ms")
                continue
            if self.restore_sustain:
                self.target.send(mido.Message("control_change", control=64, value=self.sustain))
                self.restore_sustain = False
            if note in self.sounding:
                self.target.send(mido.Message("note_off", note=note))
            adjustment = max(-6, min(6, round((material.velocity - 80) / 8 * self.source_accent))) if material else 0
            self.target.send(mido.Message("note_on", note=note, velocity=max(1, min(127, voice.velocity + adjustment))))
            if selection is not None:
                voice.melody_position = selection.selected.position
                self.melody_position = selection.next_position
                self.flow.commit(selection, voice.gesture.direction)
            voice.note = note
            self.sounding[note] = voice
            if self.report:
                g = voice.gesture
                original = self.song.source.notes[voice.melody_position % len(self.song.melody)] if material else None
                melody_debug = (f"MIDI melody index: {voice.melody_position % len(self.song.melody)}; "
                                f"Step: {voice.melody_position + 1}\n"
                                f"Original note: {original.note} ({note_name(original.note)}); "
                                f"Register-adapted material: {material.note} ({note_name(material.note)})\n"
                                f"Original contour: {material.contour} ({material.interval:+d})\n"
                                f"Source phrase: {material.phrase_break}; IOI: {material.onset_interval:g} beats\n"
                                f"Source tick: {material.tick}; Beat: {material.beat:g}; "
                                f"Duration: {material.duration:g}; Track: {material.track}; "
                                f"Channel: {material.channel + 1}\n" if material else "")
                role = ("PRIMARY chord-tone" if note % 12 in context.chord_tones else
                        "SECONDARY tension" if note % 12 in context.allowed else "ORIGINAL outside palette")
                boundary = (f"Phrase boundary detected\nReason: {reason}\nDirection: {g.direction}\n"
                            f"Rebase zone: {self.pitch.rebase_zone}\nPhrase rebase: {note_name(note)}\n"
                            if reason else "")
                flow_debug = (self.flow.debug(selection, self.song, note_name) if selection is not None else
                              "FLOW amount=0: exact melody-transform path\n" if self.flow is not None else "")
                self.report(f"{flow_debug}{melody_debug}Input note: {g.note}\nInterval: {g.interval:+d}\nDirection: {g.direction}\n"
                            f"Chord: {context.chord}; Next chord: {context.next_chord}; "
                            f"Bar: {context.bar + 1}; Beat: {context.beat + 1:.3f}\n"
                            f"Previous output: {note_name(previous)}\n{boundary}Output note: {note} ({note_name(note)})\n"
                            f"Role: {role}\n"
                            f"Harmony source: {context.harmony_source}; confidence={context.harmony_confidence:.2f}\n"
                            + ("Harmony fallback: key-scale\n" if context.harmony_source == "key-scale" else "") +
                            f"Tone priority: {self.pitch.tone_priority}; Selection: {self.pitch.selection_reason}\n"
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
        self.pitch.last_chord = None
        self.pitch.change_notes = 0
        self.melody_position = 0
        if self.flow is not None:
            self.flow.reset()
        self.last_due = 0
        self.resume_at = 0
        self.sustain = 0
        self.restore_sustain = False


def run_loopian(args):
    mode = args.loopian_mode or ("melody-transform" if args.midi_file else "gesture")
    if args.midi_file:
        if mode == "gesture":
            raise ValueError("--midi-file requires a melody mode.")
        analysis = analyze_midi(args.midi_file, args.melody_track, args.melody_channel)
        song = MidiSong(analysis.as_melody(),
                       tuple(args.chords) if args.chords is not None else None, args.tempo,
                       (args.note_min, args.note_max), adapt_register=mode != "melody-direct")
        print(format_analysis(analysis, args.chords, detailed=False), flush=True)
        if args.debug_loopian:
            print(f"Original melody median: {statistics.median(n.note for n in analysis.melody):g}\n"
                  f"Applied register shift: {song.register_shift:+d}", flush=True)
    else:
        if mode != "gesture" or args.melody_track is not None or args.melody_channel is not None:
            raise ValueError("Melody modes/selectors require --midi-file.")
        song = FixedSong(tempo=120 if args.tempo is None else args.tempo,
                         chords=tuple(args.chords) if args.chords is not None else DEFAULT_CHORDS,
                         output_range=(args.note_min, args.note_max))
    grid = args.grid if args.grid is not None else (0 if args.midi_file else 16)
    timing = Timing(grid, args.timing_strength, args.timing_window_ms / 1000)
    phrase_gap = args.phrase_gap_ms / 1000
    PitchShaper(*song.output_range, phrase_gap=phrase_gap, tone_priority=args.tone_priority)
    if not math.isfinite(args.source_accent) or not 0 <= args.source_accent <= 1:
        raise ValueError("Source accent must be finite and between 0 and 1.")
    if mode == "melody-flow":
        FlowSelector(args.flow_window, args.flow_strength, args.seed)  # Validate before opening ports.
    if args.bars < 0:
        raise ValueError("Bars must be >= 0 (0 means continuous).")
    with output_port(args.output) as target, input_port(args.input) as source:
        target.send(mido.Message("program_change", program=0))
        engine = LoopianEngine(target, song, timing,
                               (lambda line: print(line, flush=True)) if args.debug_loopian else None,
                               phrase_gap=phrase_gap, tone_priority=args.tone_priority, mode=mode,
                               flow_window=args.flow_window, flow_strength=args.flow_strength, seed=args.seed,
                               source_accent=args.source_accent)
        print(f"Loopian ready: {mode}, {song.tempo:g} BPM; grid={grid}. Ctrl+C stops.", flush=True)
        if args.midi_file:
            stepping = "FLOW local search" if engine.flow_active else "one Note On = one step"
            print(f"MIDI material: {len(song.melody)} notes; tracks={song.source.tracks}; "
                  f"{stepping}; repeats at end; harmony follows source position.", flush=True)
        start = time.perf_counter()
        last_bar = -1
        try:
            while True:
                now = time.perf_counter() - start
                context = song.context_for(engine.melody_position) if args.midi_file else song.at(now)
                # --bars remains a wall-clock run limit, including when input is silent.
                if args.bars and now * song.tempo / 60 >= args.bars * 4:
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
