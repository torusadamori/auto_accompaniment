"""Inspectable, deterministic SMF analysis; no playback or physical MIDI access.

All source times are quarter-note beats. Meter positions use the notated beat
unit. Confidence values are heuristic scores, not calibrated probabilities.
The immutable analysis model can be serialized by a future cache layer.
"""
from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from functools import cached_property
import math
import re
import statistics

import mido

from .midi_melody import MidiMelody, SourceNote


NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
QUALITIES = {"": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10),
             "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "dim": (0, 3, 6),
             "m7b5": (0, 3, 6, 10), "sus4": (0, 5, 7)}
MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)


def parse_chord(text):
    match = re.fullmatch(r"([A-G])([#b]?)(maj7|m7b5|m7|m|7|dim|sus4)?", text.strip())
    if not match:
        raise ValueError(f"Unsupported chord: {text}. Use major, m, 7, maj7, m7, dim, m7b5 or sus4.")
    letter, accidental, quality = match.groups()
    root = (dict(C=0, D=2, E=4, F=5, G=7, A=9, B=11)[letter]
            + (1 if accidental == "#" else -1 if accidental == "b" else 0)) % 12
    return root, quality or ""


def chord_palette(symbol):
    root, quality = parse_chord(symbol)
    primary = tuple((root + n) % 12 for n in QUALITIES[quality])
    # Preserve Phase 1's exact Cmaj7 / Dm7 / G7 supplemental pitch classes.
    extras = ((2, 9) if quality == "dim" else (1, 5) if quality == "m7b5" else (2, 5) if quality in ("m", "m7")
              else (2, 9))
    secondary = tuple((root + n) % 12 for n in extras if (root + n) % 12 not in primary)
    return primary, secondary


def phrase_boundary(rest, weak=0.5, strong=1.0):
    if not 0 <= weak <= strong:
        raise ValueError("Phrase rest thresholds must satisfy 0 <= weak <= strong.")
    return "STRONG" if rest >= strong else "WEAK" if rest >= weak else "NONE"


@dataclass(frozen=True)
class MeterSegment:
    beat: float
    numerator: int
    denominator: int
    bar: int

    @property
    def length(self):
        return self.numerator * 4 / self.denominator


def meter_map(signatures):
    by_beat = {0.0: (4, 4)}
    for beat, numerator, denominator in signatures:
        if numerator <= 0 or denominator <= 0:
            raise ValueError("MIDI time signature must be positive.")
        by_beat[beat] = numerator, denominator
    result = []
    for beat, (numerator, denominator) in sorted(by_beat.items()):
        if result and (numerator, denominator) == (result[-1].numerator, result[-1].denominator):
            continue  # A redundant meter event must not truncate the current bar.
        bar = 0 if not result else result[-1].bar + math.ceil((beat - result[-1].beat) / result[-1].length - 1e-9)
        result.append(MeterSegment(beat, numerator, denominator, bar))
    return tuple(result)


def meter_position(meters, beat):
    segment = meters[max(0, bisect_right([m.beat for m in meters], beat) - 1)]
    offset = max(0, beat - segment.beat)
    bar_offset = int((offset + 1e-9) // segment.length)
    within = max(0, offset - bar_offset * segment.length) / (4 / segment.denominator)
    return segment.bar + bar_offset, within, segment


def strong_beats(numerator, denominator):
    if denominator == 8 and numerator >= 6 and numerator % 3 == 0:
        return tuple(range(0, numerator, 3))
    return (0, 2) if numerator == 4 else (0,)


@dataclass(frozen=True)
class MelodyCandidate:
    track: int
    channel: int
    name: str
    programs: tuple
    count: int
    pitch_range: tuple
    average_pitch: float
    density: float
    polyphony: float
    max_polyphony: int
    overlap_ratio: float
    mean_duration: float
    velocity_deviation: float
    score: float
    percussion: bool


def score_melody(notes, name, programs):
    events = []
    for note in notes:
        if note.duration > 0:
            events.extend(((note.beat, 1), (note.beat + note.duration, -1)))
    active, union, overlap, maximum, previous = 0, 0.0, 0.0, 0, 0.0
    for beat, delta in sorted(events):  # releases before attacks at the same beat
        if active:
            union += beat - previous
            if active > 1:
                overlap += beat - previous
        active += delta
        maximum = max(maximum, active)
        previous = beat
    mean = statistics.mean(n.note for n in notes)
    duration = statistics.mean(n.duration for n in notes)
    density = len(notes) / max(1, max(n.beat + n.duration for n in notes) - notes[0].beat)
    polyphony = sum(n.duration for n in notes) / union if union else 1
    deviation = statistics.pstdev(n.velocity for n in notes)
    lower = name.lower()
    percussion = notes[0].channel == 9 or any(word in lower for word in ("drum", "percussion"))
    score = 3 - 2 * max(0, polyphony - 1) - 2 * (overlap / union if union else 0)
    score += 2 if 60 <= mean <= 84 else 0.5 if mean >= 55 else -3
    score += 1.5 if 0.3 <= density <= 4 else -1
    score += min(0.5, deviation / 20)
    score -= 2 if duration >= 4 else 0
    score -= 2 if duration < 0.12 or (len({n.note for n in notes}) <= 2 and len(notes) >= 8) else 0
    score += 4 if any(word in lower for word in ("melody", "lead", "vocal", "voice", "solo", "sax", "trumpet", "flute")) else 0
    for word, penalty in (("bass", 4), ("piano", 1), ("guitar", 1), ("chord", 3), ("accomp", 3), ("pad", 2)):
        if word in lower:
            score -= penalty
    score += 1 if any(56 <= p <= 79 for p in programs) else 0
    score -= 3 if programs and all(32 <= p <= 39 for p in programs) else 0
    if percussion:
        score -= 100
    return MelodyCandidate(notes[0].track, notes[0].channel, name, programs, len(notes),
                           (min(n.note for n in notes), max(n.note for n in notes)), mean,
                           density, polyphony, maximum, overlap / union if union else 0,
                           duration, deviation, score, percussion)


@dataclass(frozen=True)
class KeyEstimate:
    root: int
    mode: str
    confidence: float

    @property
    def name(self):
        return f"{NAMES[self.root]} {self.mode}"

    @property
    def scale(self):
        return tuple((self.root + n) % 12 for n in (MAJOR if self.mode == "major" else MINOR))


def estimate_key(notes):
    histogram = [0.0] * 12
    for note in notes:
        histogram[note.note % 12] += max(0.01, note.duration) * (0.5 + note.velocity / 254)
    total = sum(histogram)
    if not total:
        return KeyEstimate(0, "major", 0)
    ranked = []
    for root in range(12):
        for mode, scale, third in (("major", MAJOR, 4), ("minor", MINOR, 3)):
            weights = {n: 1.0 for n in scale}
            weights.update({0: 2.2, third: 1.6, 7: 1.8})
            value = sum(weight * weights.get((pc - root) % 12, -2) for pc, weight in enumerate(histogram)) / total
            ranked.append((value, root, mode))
    ranked.sort(reverse=True)
    best, second = ranked[:2]
    confidence = min(1, max(0, (best[0] + 2) / 4.2 * 0.6 + (best[0] - second[0]) * 0.4))
    return KeyEstimate(best[1], best[2], confidence)


@dataclass(frozen=True)
class HarmonySpan:
    start: float
    end: float
    chord: str | None
    confidence: float
    source: str


def infer_chord(weights, bass=None, previous=None):
    total = sum(weights)
    if total <= 0:
        return None, 0.0
    present = {i for i, weight in enumerate(weights) if weight / total >= 0.06}
    if len(present) < 3:
        return None, min(0.35, len(present) / 10)
    ranked = []
    for root in range(12):
        for quality, intervals in QUALITIES.items():
            tones = {(root + n) % 12 for n in intervals}
            coverage = sum(weights[n] for n in tones) / total
            missing = len(tones - present)
            completeness = len(tones & present) / len(tones)
            symbol = NAMES[root] + quality
            bass_support = 1 if bass == root else 0.25 if bass in tones else 0
            score = (3 * coverage + 1.5 * completeness + 0.3 * weights[root] / total
                     + 0.35 * bass_support - 1.7 * (1 - coverage) - 0.65 * missing
                     + (0.12 if previous == symbol else 0))
            ranked.append((score, symbol, coverage, completeness))
    ranked.sort(reverse=True)
    best, second = ranked[:2]
    confidence = min(1, max(0, 0.3 * best[2] + 0.3 * best[3] + 0.4 * min(1, best[0] - second[0])))
    return (best[1] if confidence >= 0.65 and best[2] >= 0.65 else None), confidence


def smooth_harmony(spans, minimum=2.0):
    """Remove brief interior excursions between the same stable harmony."""
    runs = []
    for span in spans:
        if runs and runs[-1].chord == span.chord and runs[-1].source == span.source:
            old = runs.pop()
            length = span.end - old.start
            confidence = ((old.end - old.start) * old.confidence + (span.end - span.start) * span.confidence) / length
            runs.append(replace(old, end=span.end, confidence=confidence))
        else:
            runs.append(span)
    result = list(runs)
    for index in range(1, len(runs) - 1):
        before, current, after = runs[index - 1:index + 2]
        if current.end - current.start < minimum and before.chord == after.chord and before.chord is not None:
            result[index] = replace(current, chord=before.chord, source="smoothed")
    return tuple(result)


def infer_harmony(notes, meters, length):
    ordered = sorted(notes, key=lambda n: n.beat)
    cursor, active, beat, spans, previous = 0, [], 0.0, [], None
    while beat < length - 1e-9:
        _, within, meter = meter_position(meters, beat)
        next_change = next((m.beat for m in meters if m.beat > beat + 1e-9), length)
        end = min(length, beat + 4 / meter.denominator, next_change)
        active = [n for n in active if n.beat + n.duration > beat]
        while cursor < len(ordered) and ordered[cursor].beat < end:
            if ordered[cursor].beat + ordered[cursor].duration > beat:
                active.append(ordered[cursor])
            cursor += 1
        weights = [0.0] * 12
        for note in active:
            duration = max(0, min(end, note.beat + note.duration) - max(beat, note.beat))
            accent = 1.15 if within in strong_beats(meter.numerator, meter.denominator) and beat <= note.beat < end else 1
            weights[note.note % 12] += duration * (0.5 + note.velocity / 254) * accent
        bass = min((n.note for n in active), default=None)
        chord, confidence = infer_chord(weights, None if bass is None else bass % 12, previous)
        source = "automatic" if chord else "previous" if previous else "key-scale"
        chord = chord or previous
        spans.append(HarmonySpan(beat, end, chord, confidence, source))
        if chord:
            previous = chord
        beat = end
    return smooth_harmony(spans)


@dataclass(frozen=True)
class MidiAnalysis:
    path: str
    midi_type: int
    ticks_per_beat: int
    tempo_changes: tuple
    time_signatures: tuple
    meters: tuple
    tracks: tuple
    program_changes: tuple  # (beat, track, channel, zero-based program)
    notes: tuple
    candidates: tuple
    selected: MelodyCandidate
    melody_confidence: str
    key: KeyEstimate
    harmony: tuple
    markers: tuple
    length_beats: float
    melody: tuple
    warnings: tuple

    @cached_property
    def bars(self):
        bar, within, _ = meter_position(self.meters, self.length_beats)
        return max(1, bar + int(within > 1e-8))

    @cached_property
    def harmony_starts(self):
        return tuple(h.start for h in self.harmony)

    @cached_property
    def marker_starts(self):
        return tuple(b for b, _ in self.markers)

    def harmony_at(self, beat, chords=None):
        bar, _, _ = meter_position(self.meters, beat)
        if chords:
            chord = chords[bar % len(chords)]
            parse_chord(chord)
            return HarmonySpan(beat, beat, chord, 1, "manual")
        marker_index = bisect_right(self.marker_starts, beat) - 1
        marker = self.markers[marker_index] if marker_index >= 0 else None
        if marker:
            return HarmonySpan(marker[0], self.length_beats, marker[1], 1, "marker")
        index = max(0, bisect_right(self.harmony_starts, beat) - 1)
        return self.harmony[index] if self.harmony else HarmonySpan(0, self.length_beats, None, 0, "key-scale")

    def as_melody(self):
        return MidiMelody(self.melody, self.ticks_per_beat, self.tempo_changes, self.time_signatures,
                          self.markers, self.length_beats, ((self.selected.track, self.selected.name),), self)


def analyze_midi(path, track=None, channel=None):
    try:
        midi = mido.MidiFile(path)
    except (OSError, EOFError, ValueError, KeyError) as exc:
        raise ValueError(f"Cannot read MIDI {path}: {exc}") from exc
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise ValueError("Analysis requires SMF type 0/1 with PPQN timing.")
    if track is not None and not 0 <= track < len(midi.tracks):
        raise ValueError(f"Melody track must be 0..{len(midi.tracks) - 1}.")
    if channel is not None and not 1 <= channel <= 16:
        raise ValueError("Melody channel must be 1..16.")
    ppqn = midi.ticks_per_beat
    notes, tempos, signatures, programs, markers, warnings = [], [], [], [], [], []
    names, end_tick = [], 0
    for ti, midi_track in enumerate(midi.tracks):
        names.append((ti, midi_track.name))
        tick, held, current_program = 0, defaultdict(deque), defaultdict(int)
        def finish(key, onset, order, velocity, program, ending):
            notes.append((onset, ti, order, SourceNote(onset, ending - onset, onset / ppqn,
                         (ending - onset) / ppqn, key[1], velocity, ti, key[0], program=program)))
        for order, message in enumerate(midi_track):
            tick += message.time
            if message.type == "set_tempo":
                if message.tempo <= 0:
                    raise ValueError("MIDI tempo must be positive.")
                tempos.append((tick / ppqn, ti, order, message.tempo))
            elif message.type == "time_signature":
                signatures.append((tick / ppqn, ti, order, message.numerator, message.denominator))
            elif message.type == "program_change":
                current_program[message.channel] = message.program
                programs.append((tick / ppqn, ti, message.channel, message.program))
            elif message.type in ("text", "marker"):
                text = message.text.strip()
                value = text.partition(":")[2].strip() if text.startswith("Chord:") else text
                try:
                    root, quality = parse_chord(value)
                    markers.append((tick / ppqn, ti, order, NAMES[root] + quality))
                except ValueError:
                    if text.startswith("Chord:"):
                        warnings.append(f"Ignored unsupported marker: {text}")
            elif message.type in ("note_on", "note_off"):
                key = message.channel, message.note
                if message.type == "note_on" and message.velocity:
                    held[key].append((tick, order, message.velocity, current_program[message.channel]))
                elif held[key]:
                    finish(key, *held[key].popleft(), tick)
        for key, queue in held.items():
            for onset, order, velocity, program in queue:
                finish(key, onset, order, velocity, program, tick)
                warnings.append(f"Track {ti} channel {key[0] + 1}: closed unterminated note {key[1]} at track end")
        end_tick = max(end_tick, tick)
    all_notes = tuple(n for _, _, _, n in sorted(notes, key=lambda row: row[:3]))
    signature_values = {0: (4, 4)}
    for b, _, _, n, d in sorted(signatures):
        signature_values[b] = n, d
    meters_raw = tuple((b, n, d) for b, (n, d) in sorted(signature_values.items()))
    meters = meter_map(meters_raw)
    all_notes = tuple(replace(n, bar=meter_position(meters, n.beat)[0],
                              bar_beat=meter_position(meters, n.beat)[1]) for n in all_notes)
    groups = defaultdict(list)
    for note in all_notes:
        groups[note.track, note.channel].append(note)
    candidates = []
    for (ti, ch), group in groups.items():
        used_programs = tuple(sorted({n.program for n in group}))
        candidates.append(score_melody(group, names[ti][1], used_programs))
    candidates.sort(key=lambda c: (-c.score, c.track, c.channel))
    eligible = [c for c in candidates if (track is None or c.track == track)
                and (channel is None or c.channel == channel - 1)
                and (not c.percussion or channel == 10)]
    if not eligible:
        raise ValueError("No melody notes match the selected MIDI track/channel (percussion excluded).")
    selected = eligible[0]
    confidence = "MANUAL" if track is not None or channel is not None else (
        "HIGH" if selected.score >= 4 and (len(eligible) == 1 or selected.score - eligible[1].score >= 1.25) else "LOW")
    previous, sounding_end, melody = None, 0, []
    for note in groups[selected.track, selected.channel]:
        rest = max(0, note.beat - sounding_end)
        bar, within, _ = meter_position(meters, note.beat)
        melody.append(replace(note, interval=0 if previous is None else note.note - previous.note,
                              onset_interval=0 if previous is None else note.beat - previous.beat,
                              rest_before=rest, phrase_break=phrase_boundary(rest), bar=bar, bar_beat=within))
        previous, sounding_end = note, max(sounding_end, note.beat + note.duration)
    non_drum_pairs = {(c.track, c.channel) for c in candidates if not c.percussion}
    pitched = [n for n in all_notes if (n.track, n.channel) in non_drum_pairs]
    accompaniment = [n for n in pitched if (n.track, n.channel) != (selected.track, selected.channel)]
    length = max(end_tick / ppqn, sounding_end, 0.01)
    key = estimate_key(pitched)
    harmony = infer_harmony(accompaniment or pitched, meters, length)
    tempo_values = {0: 500000}
    for b, _, _, t in sorted(tempos):
        tempo_values[b] = t
    return MidiAnalysis(str(path), midi.type, ppqn, tuple(sorted(tempo_values.items())),
                        meters_raw, meters, tuple(names), tuple(sorted(programs)), all_notes,
                        tuple(candidates), selected, confidence, key, harmony,
                        tuple((b, c) for b, _, _, c in sorted(markers)), length, tuple(melody), tuple(warnings))


def program_name(program):
    common = {0: "Acoustic Grand Piano", 24: "Nylon Guitar", 32: "Acoustic Bass", 33: "Electric Bass",
              56: "Trumpet", 64: "Soprano Sax", 65: "Alto Sax", 66: "Tenor Sax", 73: "Flute"}
    return common.get(program, f"GM program {program + 1}")


def pitch_name(note):
    return f"{NAMES[note % 12]}{note // 12 - 1}"


def format_analysis(analysis, chords=None, detailed=True):
    if chords:
        for chord in chords:
            parse_chord(chord)
    tempo = mido.tempo2bpm(analysis.tempo_changes[0][1])
    signature = analysis.meters[0]
    lines = [f"MIDI: {analysis.path}", f"Type: {analysis.midi_type}; Ticks per beat: {analysis.ticks_per_beat}",
             f"Tempo: {tempo:g} BPM; tempo map events: {len(analysis.tempo_changes)}",
             f"Time signature: {signature.numerator}/{signature.denominator}; changes: {len(analysis.time_signatures)}",
             f"Length: {analysis.bars} bars ({analysis.length_beats:g} quarter-note beats)"]
    if detailed:
        for beat, value in analysis.tempo_changes:
            lines.append(f"  Tempo at beat {beat:g}: {mido.tempo2bpm(value):g} BPM")
        for beat, n, d in analysis.time_signatures:
            lines.append(f"  Meter at beat {beat:g}: {n}/{d}")
        for ti, name in analysis.tracks:
            matching = [c for c in analysis.candidates if c.track == ti]
            lines.append(f"Track {ti}: {name or '(unnamed)'}; notes: {sum(c.count for c in matching)}")
            for c in matching:
                instruments = ', '.join(f"Percussion kit {p + 1}" if c.channel == 9 else program_name(p) for p in c.programs)
                lines.append(f"  channel: {c.channel + 1}; program: {instruments}; "
                             f"pitch: {pitch_name(c.pitch_range[0])}-{pitch_name(c.pitch_range[1])}; average pitch: {c.average_pitch:.1f}; density: {c.density:.2f}/beat; "
                             f"polyphony: {c.polyphony:.2f} (max {c.max_polyphony}); overlap: {c.overlap_ratio:.2f}; "
                             f"melody score: {c.score:.2f}{' PERCUSSION excluded' if c.percussion else ''}")
    lines.append("Melody candidates:")
    for rank, candidate in enumerate((c for c in analysis.candidates if not c.percussion), 1):
        if rank > 5:
            break
        lines.append(f"  {rank}. Track {candidate.track} channel {candidate.channel + 1} score={candidate.score:.2f} {candidate.name}")
    selected = analysis.selected
    lines.extend((f"Selected melody: Track {selected.track}, channel {selected.channel + 1}; "
                  f"program: {', '.join(program_name(p) for p in selected.programs)}",
                  f"Melody detection confidence: {analysis.melody_confidence}",
                  f"Estimated key: {analysis.key.name}; Confidence: {analysis.key.confidence:.2f}"))
    if analysis.melody_confidence == "LOW":
        lines.append("  Close or weak melody candidates; continuing. Override with --melody-track / --melody-channel if needed.")
    effective = []
    for span in analysis.harmony:
        effective.append(analysis.harmony_at(span.start, chords))
    average = statistics.mean(h.confidence for h in effective) if effective else 0
    lines.append(f"Harmony analysis: {analysis.bars} bars; average confidence: {average:.2f}")
    if detailed:
        last = None
        # Include marker onsets even when they fall between analysis beats.
        positions = sorted({s.start for s in analysis.harmony} | {b for b, _ in analysis.markers})
        for index, meter in enumerate(analysis.meters):
            end = analysis.meters[index + 1].beat if index + 1 < len(analysis.meters) else analysis.length_beats
            positions.extend(meter.beat + i * meter.length for i in range(math.ceil((end - meter.beat) / meter.length)))
        positions = sorted(set(positions))
        for beat in positions:
            span = analysis.harmony_at(beat, chords)
            bar, within, _ = meter_position(analysis.meters, beat)
            signature = (bar, span.chord, span.source)
            if signature != last:
                name = span.chord or f"{analysis.key.name} scale (Harmony fallback: key-scale)"
                lines.append(f"  Bar {bar + 1} beat {within + 1:g}: {name}; Confidence: {span.confidence:.2f}; source={span.source}")
            last = signature
        lines.append("Phrase boundaries: " + ", ".join(f"idx{i} {n.phrase_break} rest={n.rest_before:g}"
                     for i, n in enumerate(analysis.melody) if n.phrase_break != "NONE"))
    lines.extend(f"Warning: {warning}" for warning in analysis.warnings)
    return "\n".join(lines)
