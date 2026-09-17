"""Read SMF melody material without scheduling or playing it.

Track numbers are zero-based; public channel selectors are one-based.
Metadata is read from all tracks, even when notes are explicitly filtered.
"""
from collections import defaultdict, deque
from dataclasses import dataclass, replace

import mido


@dataclass(frozen=True)
class SourceNote:
    tick: int
    duration_ticks: int
    beat: float
    duration: float
    note: int
    velocity: int
    track: int
    channel: int  # MIDI wire numbering: 0..15
    interval: int = 0
    rest_before: float = 0

    @property
    def contour(self):
        return "UP" if self.interval > 0 else "DOWN" if self.interval < 0 else "SAME"


@dataclass(frozen=True)
class MidiMelody:
    notes: tuple
    ticks_per_beat: int
    tempo_changes: tuple  # (beat, microseconds per quarter note)
    time_signatures: tuple  # (beat, numerator, denominator)
    chord_markers: tuple  # (beat, chord name); authored 'Chord: Cmaj7' text/markers
    length_beats: float
    tracks: tuple  # selected (index, name)

    def tempo_at(self, beat):
        tempo = 500000
        for position, value in self.tempo_changes:
            if position > beat:
                break
            tempo = value
        return mido.tempo2bpm(tempo)


def load_melody(path, track=None, channel=None):
    """Flatten selected attacks in (tick, track, event) order; pair releases FIFO.

    With no selector, only an unambiguous non-percussion track is accepted.
    Overlapping/simultaneous notes remain separate human-triggered steps.
    """
    try:
        midi = mido.MidiFile(path)
    except (OSError, EOFError, ValueError, KeyError) as exc:
        raise ValueError(f"Cannot read MIDI melody {path}: {exc}") from exc
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise ValueError("Melody requires SMF type 0/1 with PPQN timing (not type 2/SMPTE).")
    if track is not None and not 0 <= track < len(midi.tracks):
        raise ValueError(f"Melody track must be 0..{len(midi.tracks) - 1}.")
    if channel is not None and not 1 <= channel <= 16:
        raise ValueError("Melody channel must be 1..16.")
    events, tempos, meters, chords, track_names = [], [], [], [], []
    end_tick = 0
    for track_index, midi_track in enumerate(midi.tracks):
        tick = 0
        track_names.append((track_index, midi_track.name))
        for order, message in enumerate(midi_track):
            tick += message.time
            if message.type == "set_tempo":
                if message.tempo <= 0:
                    raise ValueError("MIDI tempo must be positive.")
                tempos.append((tick, track_index, order, message.tempo))
            elif message.type == "time_signature":
                meters.append((tick, track_index, order, message.numerator, message.denominator))
            elif message.type in ("marker", "text") and message.text.startswith("Chord:"):
                chords.append((tick, track_index, order, message.text.partition(":")[2].strip()))
            elif message.type in ("note_on", "note_off"):
                events.append((tick, track_index, order, message))
        end_tick = max(end_tick, tick)
    if any((n, d) != (4, 4) for _, _, _, n, d in meters):
        raise ValueError("Loopian Phase 2 currently supports 4/4 MIDI only.")
    selected = [e for e in events if (track is None or e[1] == track)
                and (e[3].channel == channel - 1 if channel is not None else e[3].channel != 9)]
    note_tracks = sorted({t for _, t, _, m in selected if m.type == "note_on" and m.velocity})
    if track is None and channel is None and len(note_tracks) > 1:
        names = ", ".join(f"{t}: {track_names[t][1] or '(unnamed)'}" for t in note_tracks)
        raise ValueError(f"Multiple melody tracks ({names}); specify --melody-track or --melody-channel.")
    held, notes = defaultdict(deque), []
    for tick, track_index, order, message in sorted(selected, key=lambda e: e[:3]):
        key = (track_index, message.channel, message.note)
        if message.type == "note_on" and message.velocity:
            held[key].append((tick, order, message.velocity))
        elif held[key]:
            onset, attack_order, velocity = held[key].popleft()
            notes.append((onset, track_index, attack_order, SourceNote(
                onset, tick - onset, onset / midi.ticks_per_beat,
                (tick - onset) / midi.ticks_per_beat, message.note, velocity,
                track_index, message.channel)))
    if any(held.values()):
        raise ValueError("Selected MIDI melody contains Note On without a matching Note Off.")
    if not notes:
        raise ValueError("No melody notes match the selected MIDI track/channel.")
    ordered = [entry[3] for entry in sorted(notes, key=lambda e: e[:3])]
    result, previous, sounding_end = [], None, 0
    for note in ordered:
        result.append(replace(note, interval=0 if previous is None else note.note - previous.note,
                              rest_before=max(0, note.beat - sounding_end)))
        previous = note
        sounding_end = max(sounding_end, note.beat + note.duration)
    ppqn = midi.ticks_per_beat
    return MidiMelody(tuple(result), ppqn,
                      tuple((t / ppqn, value) for t, _, _, value in sorted(tempos)),
                      tuple((t / ppqn, n, d) for t, _, _, n, d in sorted(meters)) or ((0, 4, 4),),
                      tuple((t / ppqn, value) for t, _, _, value in sorted(chords)),
                      max(end_tick / ppqn, sounding_end),
                      tuple(track_names[t] for t in note_tracks))
