"""SMF ingestion, human-driven melody stepping, transformation and real CLI."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mido

from autoaccomp.loopian import (Gesture, LoopianEngine, MidiSong, PALETTES, PRIMARY,
                               PitchShaper, Timing, run_loopian)
from autoaccomp.main import main, parser
from autoaccomp.midi_melody import load_melody
from test_melody_cli import Backend, Clock, Port


DEMO = Path(__file__).resolve().parents[1] / "examples" / "loopian_phase2.mid"
NOTES = [60, 64, 67, 69, 65, 69, 72, 74, 71, 74, 77, 76, 67, 64, 62, 60]


def note_track(notes, channel=0):
    track = mido.MidiTrack()
    for note in notes:
        track.extend((mido.Message("note_on", note=note, channel=channel, velocity=81),
                      mido.Message("note_off", note=note, channel=channel, time=480)))
    return track


class MidiLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "song.mid"

    def save(self, tracks, midi_type=1):
        midi = mido.MidiFile(type=midi_type, ticks_per_beat=480)
        midi.tracks.extend(tracks)
        midi.save(self.path)
        return self.path

    def test_demo_metadata_notes_durations_and_contour(self):
        source = load_melody(DEMO)
        self.assertEqual([n.note for n in source.notes], NOTES)
        self.assertEqual([n.interval for n in source.notes[:5]], [0, 4, 3, 2, -4])
        self.assertEqual([n.tick for n in source.notes], list(range(0, 7680, 480)))
        self.assertTrue(all(n.duration == 1 and n.duration_ticks == 480 for n in source.notes))
        self.assertEqual(source.tempo_at(0), 120)
        self.assertEqual(source.time_signatures, ((0, 4, 4),))
        self.assertEqual(source.chord_markers, ((0, "Cmaj7"), (4, "Dm7"), (8, "G7"), (12, "Cmaj7")))
        self.assertEqual(source.length_beats, 16)
        self.assertEqual(source.tracks, ((1, "Melody (channel 1)"),))

    def test_selectors_filter_notes_but_not_conductor_metadata(self):
        conductor = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=750000),
                                    mido.MetaMessage("set_tempo", tempo=1000000, time=480)])
        self.save([conductor, note_track([60, 62]), note_track([72, 74], 1)])
        with self.assertRaisesRegex(ValueError, "Multiple melody tracks"):
            load_melody(self.path)
        a = load_melody(self.path, track=1)
        b = load_melody(self.path, channel=2)
        self.assertEqual([n.note for n in a.notes], [60, 62])
        self.assertEqual([n.note for n in b.notes], [72, 74])
        self.assertTrue(all(n.channel == 1 and n.track == 2 for n in b.notes))
        self.assertEqual((a.tempo_at(0), a.tempo_at(1)), (80, 60))
        self.assertEqual(load_melody(self.path, track=2, channel=2), b)
        with self.assertRaisesRegex(ValueError, "No melody"):
            load_melody(self.path, track=1, channel=2)

    def test_type_zero_channels_and_percussion(self):
        track = note_track([36], 9) + note_track([67], 0)
        self.save([mido.MidiTrack(track)], midi_type=0)
        self.assertEqual([n.note for n in load_melody(self.path).notes], [67])
        self.assertEqual([n.note for n in load_melody(self.path, channel=10).notes], [36])

    def test_same_pitch_fifo_velocity_zero_and_rest(self):
        self.save([mido.MidiTrack([
            mido.Message("note_off", note=80),  # unrelated release is ignored
            mido.Message("note_on", note=60, velocity=90, time=480),
            mido.Message("note_on", note=60, velocity=80, time=120),
            mido.Message("note_on", note=60, velocity=0, time=120),
            mido.Message("note_off", note=60, time=120),
            mido.Message("note_on", note=64, velocity=70, time=600),
            mido.Message("note_off", note=64, time=480),
        ])])
        notes = load_melody(self.path).notes
        self.assertEqual([n.duration_ticks for n in notes], [240, 240, 480])
        self.assertEqual([n.velocity for n in notes], [90, 80, 70])
        self.assertEqual([n.rest_before for n in notes], [1, 0, 1.25])

    def test_simultaneous_polyphony_has_stable_step_order(self):
        self.save([mido.MidiTrack([
            mido.Message("note_on", note=67), mido.Message("note_on", note=60),
            mido.Message("note_off", note=60, time=240), mido.Message("note_off", note=67),
        ])])
        self.assertEqual([n.note for n in load_melody(self.path).notes], [67, 60])

    def test_invalid_file_selection_and_unsupported_formats(self):
        with self.assertRaisesRegex(ValueError, "Cannot read MIDI"):
            load_melody(self.path)
        self.path.write_bytes(b"not a midi file")
        with self.assertRaises(ValueError):
            load_melody(self.path)
        self.save([note_track([60])])
        for kwargs in ({"track": -1}, {"track": 2}, {"channel": 0}, {"channel": 17}):
            with self.assertRaises(ValueError):
                load_melody(self.path, **kwargs)
        self.save([note_track([60])], midi_type=2)
        with self.assertRaisesRegex(ValueError, "SMF type"):
            load_melody(self.path)
        self.save([mido.MidiTrack([mido.MetaMessage("time_signature", numerator=3, denominator=4)]), note_track([60])])
        with self.assertRaisesRegex(ValueError, "4/4"):
            load_melody(self.path)
        self.save([mido.MidiTrack()])
        with self.assertRaisesRegex(ValueError, "No melody"):
            load_melody(self.path)
        self.save([mido.MidiTrack([mido.Message("note_on", note=60)])])
        with self.assertRaisesRegex(ValueError, "matching Note Off"):
            load_melody(self.path)

    def test_source_context_tempo_chord_override_and_repeats(self):
        source = load_melody(DEMO)
        song = MidiSong(source)
        self.assertEqual([song.context_for(i).chord for i in (0, 4, 8, 12, 16)],
                         ["Cmaj7", "Dm7", "G7", "Cmaj7", "Cmaj7"])
        self.assertEqual((song.context_for(17).bar, song.context_for(17).beat), (4, 1))
        self.assertEqual(MidiSong(source, chords=("Dm7",)).context_for(0).chord, "Dm7")
        source = replace(source, tempo_changes=((0, 750000), (4, 1000000)))
        self.assertEqual(MidiSong(source).context_for(4).tempo, 60)
        self.assertEqual(MidiSong(source, tempo_override=100).context_for(4).tempo, 100)
        source = replace(source, chord_markers=())
        self.assertEqual(MidiSong(source).context_for(8).chord, "G7")
        source = replace(source, chord_markers=((0, "Am"),))
        with self.assertRaisesRegex(ValueError, "Chord:"):
            MidiSong(source)
        self.assertEqual(MidiSong(source, chords=("Cmaj7",)).context_for(0).chord, "Cmaj7")


class MelodyEngineTests(unittest.TestCase):
    def engine(self, mode="melody-transform", **kwargs):
        port = Port(Clock())
        song = MidiSong(load_melody(DEMO))
        return LoopianEngine(port, song, Timing(grid=0), mode=mode, **kwargs), port

    def attack(self, engine, note, now):
        engine.receive(mido.Message("note_on", note=note, velocity=87), now)
        engine.tick(now)

    def test_direct_human_steps_timing_velocity_release_and_wrap(self):
        engine, port = self.engine("melody-direct")
        engine.tick(100)
        self.assertEqual(port.messages, [])
        self.assertEqual(engine.melody_position, 0)
        for i in range(18):
            now = i * 0.37
            self.attack(engine, 31, now)
            self.assertEqual(port.messages[-1].note, NOTES[i % 16])
            self.assertEqual(port.messages[-1].velocity, 87)
            self.assertEqual(engine.melody_position, i + 1)
            engine.receive(mido.Message("note_on", note=31, velocity=0), now + 0.2)
            engine.tick(now + 0.2)
            self.assertEqual(port.messages[-1].type, "note_off")
            self.assertEqual(port.messages[-1].note, NOTES[i % 16])
            self.assertEqual(engine.melody_position, i + 1)
        self.assertFalse(engine.sounding or engine.held)

    def test_direct_preserves_outside_palette_and_range(self):
        engine, port = self.engine("melody-direct")
        notes = (replace(engine.song.melody[0], note=37),)
        engine.song = MidiSong(replace(engine.song.source, notes=notes))
        self.attack(engine, 90, 0)
        self.assertEqual(port.messages[-1].note, 37)

    def test_transform_follows_down_against_rising_source_and_same(self):
        engine, port = self.engine()
        outputs = []
        for i, key in enumerate((80, 79, 78, 78)):
            self.attack(engine, key, i * 0.1)
            outputs.append(port.messages[-1].note)
        self.assertGreater(outputs[0], outputs[1])
        self.assertGreater(outputs[1], outputs[2])
        self.assertEqual(outputs[2], outputs[3])
        self.assertEqual(engine.melody_position, 4)
        self.assertTrue(all(n % 12 in PALETTES["Cmaj7"] for n in outputs))

    def test_original_contour_has_audible_effect_and_can_transpose(self):
        source = load_melody(DEMO)
        song = MidiSong(source, chords=("Dm7",))
        context = song.context_for(1)
        def choose(interval):
            shaper = PitchShaper()
            shaper.previous = 65
            return shaper.choose(Gesture(62, 2, "UP", 0.1), context,
                                 material=replace(source.notes[1], interval=interval, note=69))
        self.assertEqual(choose(4), 69)  # F -> A preserves the source C -> E movement
        self.assertEqual(choose(1), 67)  # Different source contour, identical human gesture

    def test_transform_keeps_strong_beat_chord_priority_and_flat_comparison(self):
        source = load_melody(DEMO)
        context = MidiSong(source).context_for(3)  # A4, source interval +2, weak beat
        def choose(beat, priority):
            shaper = PitchShaper(tone_priority=priority)
            shaper.previous = 67
            return shaper.choose(Gesture(62, 2, "UP", 0.1), replace(context, beat=beat),
                                 material=source.notes[3])
        self.assertEqual(choose(3, "chord"), 69)
        self.assertEqual(choose(0, "chord"), 71)
        self.assertEqual(choose(2, "chord"), 71)
        self.assertEqual(choose(0, "flat"), 69)

    def test_quantized_short_tap_keeps_reserved_material_until_release(self):
        engine, port = self.engine("melody-direct")
        engine.timing = Timing(grid=16)
        engine.receive(mido.Message("note_on", note=30), 0.110)
        engine.receive(mido.Message("note_on", note=30, velocity=0), 0.112)
        self.assertEqual(engine.melody_position, 1)
        engine.tick(0.117)
        self.assertFalse(port.messages)
        engine.tick(0.118)
        self.assertEqual((port.messages[-1].type, port.messages[-1].note), ("note_on", 60))
        engine.tick(0.120)
        self.assertEqual((port.messages[-1].type, port.messages[-1].note), ("note_off", 60))
        self.assertEqual(engine.melody_position, 1)
        self.assertFalse(engine.sounding or engine.held or engine.queue)

    def test_harmony_follows_source_not_human_speed(self):
        for spacing in (0.05, 0.2):
            engine, port = self.engine()
            for i in range(12):
                self.attack(engine, 40 + i, 50 + i * spacing)
                context = engine.song.context_for(i)
                self.assertIn(port.messages[-1].note % 12, PALETTES[context.chord])
                if i in (4, 8):
                    self.assertIn(port.messages[-1].note % 12, PRIMARY[context.chord])
            self.assertEqual(engine.pitch.last_chord, "G7")

    def test_outside_notes_snap_and_results_are_deterministic(self):
        outputs = []
        for base in (30, 60):
            engine, port = self.engine()
            source = engine.song.source
            engine.song = MidiSong(replace(source, notes=tuple(replace(n, note=66) for n in source.notes)))
            for i, offset in enumerate((0, 1, 2, 1, 0, -1, 0, 1)):
                self.attack(engine, base + offset, i * 0.1)
                self.assertIn(port.messages[-1].note % 12, PALETTES[engine.song.context_for(i).chord])
            outputs.append([m.note for m in port.messages if m.type == "note_on"])
        self.assertEqual(outputs[0], outputs[1])

    def test_pause_continues_material_and_reset_rewinds(self):
        engine, port = self.engine()
        self.attack(engine, 60, 0)
        self.attack(engine, 62, 2)
        self.assertEqual(engine.pitch.boundary_reason, "INPUT_PAUSE")
        self.assertEqual(engine.melody_position, 2)
        engine.receive(mido.Message("control_change", control=123), 2.1)
        self.assertEqual(engine.melody_position, 0)
        self.assertFalse(engine.queue or engine.held or engine.sounding)

    def test_boundary_deferred_short_taps_do_not_skip_material(self):
        logs = []
        engine, port = self.engine(report=logs.append)
        self.attack(engine, 60, 0)
        engine.pitch.previous = 96
        self.attack(engine, 61, 0.1)
        self.assertEqual(engine.melody_position, 2)
        engine.receive(mido.Message("note_off", note=61), 0.105)
        self.attack(engine, 62, 0.11)
        engine.receive(mido.Message("note_off", note=62), 0.115)
        self.assertEqual(engine.melody_position, 3)
        engine.tick(0.17)
        self.assertEqual(sum(m.type == "note_on" for m in port.messages), 3)
        self.assertEqual(engine.melody_position, 3)
        engine.tick(0.2)
        self.assertFalse(engine.sounding)
        self.assertEqual(sum("MIDI melody index:" in line for line in logs), 3)
        self.assertIn("MIDI melody index: 1; Step: 2", "\n".join(logs))
        self.assertIn("MIDI melody index: 2; Step: 3", "\n".join(logs))

    def test_direction_and_range_all_chords_source_intervals(self):
        material = load_melody(DEMO).notes[0]
        for chord in PALETTES:
            context = MidiSong(load_melody(DEMO), chords=(chord,)).context_for(0)
            for previous in range(48, 97):
                for sign, direction in ((1, "UP"), (-1, "DOWN")):
                    for interval in (-12, -4, 0, 2, 7):
                        shaper = PitchShaper()
                        shaper.previous = previous
                        note = shaper.choose(Gesture(60, sign, direction, 0.1), context,
                                             material=replace(material, interval=interval))
                        self.assertIn(note % 12, PALETTES[chord])
                        self.assertTrue(48 <= note <= 96)
                        if shaper.boundary_reason is None:
                            self.assertGreater(sign * (note - previous), 0)
                            self.assertLessEqual(abs(note - previous), 5)


class MelodyMidiCliTests(unittest.TestCase):
    def exercise(self, flags):
        clock = Clock()
        source, target = Port(clock), Port(clock)
        for i, note in enumerate((30, 31, 32, 33, 34, 33, 32, 31)):
            source.events.extend(((i * 0.15 + 0.01, mido.Message("note_on", note=note, velocity=83)),
                                  (i * 0.15 + 0.1, mido.Message("note_off", note=note))))
        argv = ["autoaccomp", "loopian", "--input", "Keyboard", "--output", "Synth", "--bars", "1",
                "--midi-file", str(DEMO), "--debug-loopian", *flags]
        logs = io.StringIO()
        with patch("sys.argv", argv), patch("time.perf_counter", clock.counter), \
             patch("time.sleep", clock.sleep), patch("autoaccomp.midi_io.backend", return_value=Backend(source, target)), \
             redirect_stdout(logs):
            main()
        self.assertTrue(source.closed and target.closed and target.reset_called and target.panic_called)
        return target.messages, logs.getvalue()

    def test_direct_and_transform_cli(self):
        direct, log = self.exercise(["--loopian-mode", "melody-direct", "--melody-track", "1"])
        pitches = lambda messages: [m.note for m in messages if m.type == "note_on"]
        self.assertEqual(pitches(direct), NOTES[:8])
        self.assertIn("grid=0", log)
        self.assertIn("Original contour: UP (+4)", log)
        self.assertIn("MIDI melody index: 7", log)
        transformed, log = self.exercise(["--melody-channel", "1"])
        self.assertNotEqual(pitches(direct), pitches(transformed))
        self.assertIn("melody-transform", log)
        self.assertIn("Role: PRIMARY chord-tone", log)
        self.assertEqual(len(pitches(transformed)), 8)
        self.assertEqual(sum(m.type == "note_off" for m in transformed), 8)

    def test_invalid_combinations_fail_before_ports_open(self):
        base = ["loopian", "--input", "Keyboard", "--output", "Synth"]
        for flags in (["--loopian-mode", "melody-direct"], ["--melody-track", "1"],
                      ["--midi-file", str(DEMO), "--loopian-mode", "gesture"],
                      ["--midi-file", str(DEMO), "--melody-track", "99"]):
            with patch("autoaccomp.loopian.output_port") as port, self.assertRaises(ValueError):
                run_loopian(parser().parse_args(base + flags))
            port.assert_not_called()


if __name__ == "__main__":
    unittest.main()
