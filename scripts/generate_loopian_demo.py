"""Generate the authored Phase 2 audition fixture (no borrowed MIDI content)."""
from pathlib import Path
import mido


def generate(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    conductor = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Loopian demo conductor"),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120)),
        mido.MetaMessage("time_signature", numerator=4, denominator=4),
    ])
    for index, chord in enumerate(("Cmaj7", "Dm7", "G7", "Cmaj7")):
        conductor.append(mido.MetaMessage("marker", text=f"Chord: {chord}", time=0 if index == 0 else 1920))
    conductor.append(mido.MetaMessage("end_of_track", time=1920))
    melody = mido.MidiTrack([mido.MetaMessage("track_name", name="Melody (channel 1)")])
    for index, note in enumerate((60, 64, 67, 69, 65, 69, 72, 74, 71, 74, 77, 76, 67, 64, 62, 60)):
        melody.append(mido.Message("note_on", note=note, velocity=96 if index % 4 == 0 else 80))
        melody.append(mido.Message("note_off", note=note, time=480))
    midi.tracks.extend((conductor, melody))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(path)


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "examples" / "loopian_phase2.mid")
