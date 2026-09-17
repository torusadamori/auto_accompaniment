"""Original, redistributable Phase 4 fixtures; not transcriptions of real songs."""
from pathlib import Path
import mido


def track(name, events, end):
    result = mido.MidiTrack([mido.MetaMessage("track_name", name=name)])
    previous = 0
    for beat, message in sorted(events, key=lambda item: (item[0], item[1].type == "note_on")):
        tick = round(beat * 480)
        result.append(message.copy(time=tick - previous))
        previous = tick
    result.append(mido.MetaMessage("end_of_track", time=max(0, round(end * 480) - previous)))
    return result


def notes(pitches, starts, durations, channel, velocity=80):
    events = []
    for index, (pitch, start, duration) in enumerate(zip(pitches, starts, durations)):
        events.extend(((start, mido.Message("note_on", channel=channel, note=pitch, velocity=velocity + index % 3 * 4)),
                       (start + duration, mido.Message("note_off", channel=channel, note=pitch))))
    return events


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    basic = [(0, mido.MetaMessage("set_tempo", tempo=500000)),
             (0, mido.MetaMessage("time_signature", numerator=4, denominator=4))]
    melody = notes([72, 76, 79, 76, 74, 77, 81, 77], range(8), [0.7] * 8, 0)
    chords = []
    for start, pitches in ((0, [48, 52, 55]), (4, [50, 53, 57])):
        chords += notes(pitches, [start] * 3, [4] * 3, 1, 68)
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    midi.tracks.append(track("Type 0 ensemble", basic + [(0, mido.Message("program_change", channel=0, program=73))] + melody + chords, 8))
    midi.save(directory / "loopian_type0.mid")

    # 3/4 for two bars, then 4/4; a tempo change and near-tied melody candidates.
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    conductor = [(0, mido.MetaMessage("set_tempo", tempo=600000)),
                 (0, mido.MetaMessage("time_signature", numerator=3, denominator=4)),
                 (6, mido.MetaMessage("set_tempo", tempo=500000)),
                 (6, mido.MetaMessage("time_signature", numerator=4, denominator=4))]
    lead = notes([69, 72, 76, 74, 72, 69, 71, 74, 77, 76, 72, 76, 79, 72], range(14), [0.5] * 14, 0)
    midi.tracks.extend((track("Conductor", conductor, 14), track("Lead A", lead, 14),
                        track("Lead B", [(b, m.copy(channel=1)) for b, m in lead], 14)))
    midi.save(directory / "loopian_type1.mid")

    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    pitches = [72, 76, 79, 83, 69, 72, 76, 74, 77, 81, 84, 71, 74, 77, 76]
    starts = [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15]
    lead = notes(pitches, starts, [0.5] * len(pitches), 0)
    lead.insert(0, (0, mido.Message("program_change", channel=0, program=65)))
    piano, bass = [(0, mido.Message("program_change", channel=1, program=0))], [(0, mido.Message("program_change", channel=2, program=32))]
    for start, chord, root in ((0, [60, 64, 67, 71], 36), (4, [57, 60, 64, 67], 33),
                               (8, [62, 65, 69, 72], 38), (12, [59, 62, 65, 67], 31)):
        piano += notes(chord, [start] * 4, [4] * 4, 1, 65)
        bass += notes([root], [start], [4], 2, 76)
    drums = notes([36, 42, 38, 42] * 8, [i / 2 for i in range(32)], [0.1] * 32, 9)
    midi.tracks.extend((track("Conductor", basic, 16), track("Melody Alto Sax", lead, 16),
                        track("Piano accompaniment", piano, 16), track("Bass", bass, 16), track("Drums", drums, 16)))
    midi.save(directory / "loopian_gm.mid")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "examples")
