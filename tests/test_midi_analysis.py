"""Phase 4 analysis and FLOW integration using original generated SMFs."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mido

from autoaccomp.loopian import Gesture, LoopianEngine, MidiSong, PitchShaper, Timing
from autoaccomp.loopian_flow import FlowSelector
from autoaccomp.main import main, parser
from autoaccomp.midi_analysis import (HarmonySpan, analyze_midi, chord_palette, estimate_key,
                                     format_analysis, infer_chord, meter_map, meter_position,
                                     phrase_boundary, smooth_harmony)
from autoaccomp.midi_melody import SourceNote
from test_melody_cli import Backend, Clock, Port
from test_loopian_midi import note_track


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "song.mid"

    def save(self, tracks, midi_type=1):
        midi = mido.MidiFile(type=midi_type, ticks_per_beat=480)
        midi.tracks.extend(tracks)
        midi.save(self.path)
        return self.path

    def test_type_zero_selects_channel_not_whole_polyphonic_track(self):
        analysis = analyze_midi(EXAMPLES / "loopian_type0.mid")
        self.assertEqual(analysis.midi_type, 0)
        self.assertEqual((analysis.selected.track, analysis.selected.channel), (0, 0))
        self.assertEqual(len(analysis.melody), 8)
        self.assertEqual(analysis.harmony_at(0).chord, "C")
        self.assertEqual(analysis.harmony_at(4).chord, "Dm")
        self.assertEqual(analysis.selected.programs, (73,))

    def test_gm_melody_scoring_statistics_and_percussion_exclusion(self):
        analysis = analyze_midi(EXAMPLES / "loopian_gm.mid")
        self.assertEqual(analysis.selected.track, 1)
        self.assertEqual(analysis.melody_confidence, "HIGH")
        by_track = {c.track: c for c in analysis.candidates}
        self.assertEqual(by_track[1].polyphony, 1)
        self.assertEqual(by_track[2].max_polyphony, 4)
        self.assertEqual(by_track[2].overlap_ratio, 1)
        self.assertLess(by_track[3].average_pitch, 48)
        self.assertTrue(by_track[4].percussion)
        self.assertGreater(by_track[1].score, by_track[2].score)
        self.assertGreater(by_track[1].velocity_deviation, 0)
        self.assertEqual([analysis.harmony_at(b).chord for b in (0, 4, 8, 12)], ["Cmaj7", "Am7", "Dm7", "G7"])
        self.assertTrue(all(0 <= h.confidence <= 1 for h in analysis.harmony))
        self.assertEqual(analysis.key.name, "C major")
        self.assertIn((0, 1, 0, 65), analysis.program_changes)

    def test_type_one_tempo_meter_changes_and_low_confidence_continue(self):
        analysis = analyze_midi(EXAMPLES / "loopian_type1.mid")
        self.assertEqual(analysis.midi_type, 1)
        self.assertEqual(analysis.tempo_changes, ((0, 600000), (6, 500000)))
        self.assertEqual(analysis.time_signatures, ((0, 3, 4), (6, 4, 4)))
        self.assertEqual(analysis.bars, 4)
        self.assertEqual(analysis.melody_confidence, "LOW")
        self.assertEqual((analysis.melody[5].bar, analysis.melody[5].bar_beat), (1, 2))
        self.assertEqual((analysis.melody[6].bar, analysis.melody[6].bar_beat), (2, 0))
        self.assertEqual((analysis.notes[-1].bar, analysis.notes[-1].bar_beat), (3, 3))
        song = MidiSong(analysis.as_melody())
        self.assertEqual(song.context_for(5).tempo, 100)
        self.assertEqual(song.context_for(6).tempo, 120)
        self.assertEqual(song.context_for(5).accents, (0,))
        self.assertEqual(song.context_for(6).accents, (0, 2))
        self.assertEqual(song.context_for(14).bar, 4)

    def test_manual_track_channel_overrides_auto(self):
        analysis = analyze_midi(EXAMPLES / "loopian_gm.mid", track=3)
        self.assertEqual(analysis.selected.track, 3)
        self.assertEqual(analysis.melody_confidence, "MANUAL")
        analysis = analyze_midi(EXAMPLES / "loopian_type0.mid", channel=2)
        self.assertEqual(analysis.selected.channel, 1)
        with self.assertRaises(ValueError):
            analyze_midi(EXAMPLES / "loopian_gm.mid", track=1, channel=2)

    def test_eight_chord_qualities_all_roots_and_inversions(self):
        for symbol in ("C", "Dm", "G7", "Cmaj7", "Am7", "Bdim", "Bm7b5", "Dsus4",
                       "F#", "Bbm", "Ebmaj7", "F#m7b5"):
            tones, _ = chord_palette(symbol)
            weights = [1 if pc in tones else 0 for pc in range(12)]
            chord, confidence = infer_chord(weights, tones[0])
            self.assertEqual(chord, symbol)
            self.assertGreater(confidence, 0.65)
        weights = [1 if pc in (0, 4, 7) else 0 for pc in range(12)]
        self.assertEqual(infer_chord(weights, 4)[0], "C")  # C/E, not an E-root chord
        weights[2] = 0.6  # Add a passing D so confidence is not already saturated.
        self.assertGreater(infer_chord(weights, 0)[1], infer_chord(weights, 6)[1])
        # A triad's first SECONDARY must not be mislabeled as a fourth chord tone.
        self.assertEqual(chord_palette("C"), ((0, 4, 7), (2, 9)))
        self.assertEqual(chord_palette("Dm7"), ((2, 5, 9, 0), (4, 7)))

    def test_smoothing_removes_short_excursion_but_keeps_real_changes(self):
        spans = [HarmonySpan(0, 1, "C", 0.9, "automatic"), HarmonySpan(1, 2, "C", 0.9, "automatic"),
                 HarmonySpan(2, 3, "Cmaj7", 0.8, "automatic"), HarmonySpan(3, 4, "C", 0.9, "automatic")]
        result = smooth_harmony(spans)
        self.assertEqual({s.chord for s in result}, {"C"})
        self.assertTrue(any(s.source == "smoothed" for s in result))
        sustained = [spans[0], HarmonySpan(1, 3, "Dm", 0.9, "automatic"), spans[-1]]
        self.assertEqual(smooth_harmony(sustained)[1].chord, "Dm")

    def test_key_major_minor_and_low_harmony_fallback(self):
        def key(pitches):
            return estimate_key([SourceNote(0, 480, 0, 1, pitch, 80, 0, 0) for pitch in pitches])
        self.assertEqual(key([60, 64, 67] * 4).name, "C major")
        self.assertEqual(key([57, 60, 64] * 4).name, "A minor")
        self.save([note_track([60, 64, 67, 64])])
        analysis = analyze_midi(self.path)
        self.assertTrue(all(s.chord is None for s in analysis.harmony))
        context = MidiSong(analysis.as_melody()).context_for(0)
        self.assertEqual(context.harmony_source, "key-scale")
        self.assertEqual(context.chord_tones, ())
        self.assertEqual(set(context.allowed), {0, 2, 4, 5, 7, 9, 11})
        self.assertIn("Harmony fallback: key-scale", format_analysis(analysis))
        shaper = PitchShaper()
        self.assertIn(shaper.choose(Gesture(30, 0, "START", None), context) % 12, context.allowed)

    def test_low_confidence_continues_previous_chord(self):
        melody = note_track([72] * 8)
        piano = mido.MidiTrack([mido.MetaMessage("track_name", name="Piano")])
        for pitch in (60, 64, 67):
            piano.append(mido.Message("note_on", note=pitch, channel=1))
        for i, pitch in enumerate((60, 64, 67)):
            piano.append(mido.Message("note_off", note=pitch, channel=1, time=1920 if i == 0 else 0))
        piano.extend((mido.Message("note_on", note=60, channel=1), mido.Message("note_off", note=60, channel=1, time=1920)))
        self.save([melody, piano])
        analysis = analyze_midi(self.path, track=0)
        self.assertEqual(analysis.harmony_at(6).chord, "C")
        self.assertEqual(analysis.harmony_at(6).source, "previous")
        self.assertLess(analysis.harmony_at(6).confidence, 1)

    def test_markers_and_explicit_chords_override_inference(self):
        midi = mido.MidiFile(EXAMPLES / "loopian_gm.mid")
        midi.tracks[0].insert(1, mido.MetaMessage("marker", text="Ebmaj7"))
        midi.tracks[0].insert(2, mido.MetaMessage("text", text="Chord: Bm7b5"))
        midi.save(self.path)
        analysis = analyze_midi(self.path)
        self.assertEqual(analysis.harmony_at(0).chord, "Bm7b5")
        self.assertEqual(analysis.harmony_at(0).source, "marker")
        song = MidiSong(analysis.as_melody(), chords=("F#7", "Bbm"))
        context = song.context_for(0)
        self.assertEqual(context.chord, "F#7")
        self.assertEqual(context.harmony_source, "manual")
        self.assertEqual(context.chord_tones, (6, 10, 1, 4))
        self.assertIn("F#7", format_analysis(analysis, chords=("F#7",)))

    def test_phrase_breaks_ioi_and_flow_crossing_penalty(self):
        self.assertEqual([phrase_boundary(n) for n in (0.49, 0.5, 0.99, 1)], ["NONE", "WEAK", "WEAK", "STRONG"])
        self.assertEqual(phrase_boundary(0.4, weak=0.25, strong=0.5), "WEAK")
        analysis = analyze_midi(EXAMPLES / "loopian_gm.mid")
        self.assertEqual(analysis.melody[7].phrase_break, "STRONG")
        self.assertEqual(analysis.melody[7].onset_interval, 2)
        # Create a within-bar phrase break so the independent phrase cost is observable.
        source = analysis.as_melody()
        material = tuple(replace(n, phrase_break="STRONG" if i == 2 else "NONE") for i, n in enumerate(source.notes))
        song = MidiSong(replace(source, notes=material))
        pitch = PitchShaper()
        pitch.previous, pitch.last_chord = 64, "Cmaj7"
        choice = FlowSelector(strength=1, seed=1).select(pitch, song, Gesture(67, 7, "UP", 0.25), 1, {})
        self.assertTrue(all(c.phrase_cost >= 3 for c in choice.candidates if c.position >= 2))
        self.assertTrue(any(c.position == 2 for c in choice.candidates))

    def test_compound_meter_and_partial_bar_change(self):
        self.assertEqual(meter_position(meter_map(((0, 4, 4), (2, 4, 4))), 3)[:2], (0, 3))
        meters = meter_map(((0, 6, 8), (6, 3, 4), (10, 4, 4)))
        self.assertEqual(meter_position(meters, 1.5)[:2], (0, 3))
        self.assertEqual(meter_position(meters, 6)[:2], (2, 0))
        self.assertEqual(meter_position(meters, 10)[:2], (4, 0))  # partial 3/4 bar closed
        conductor = mido.MidiTrack([mido.MetaMessage("time_signature", numerator=6, denominator=8)])
        self.save([conductor, note_track([60, 62, 64, 65, 67, 69])])
        song = MidiSong(analyze_midi(self.path).as_melody())
        self.assertEqual(song.context_for(0).accents, (0, 3))
        self.assertEqual((song.context_for(3).bar, song.context_for(3).beat), (1, 0))

    def test_delayed_metadata_defaults_and_program_changes(self):
        conductor = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=750000, time=480),
                                    mido.MetaMessage("time_signature", numerator=3, denominator=4, time=480)])
        lead = note_track([60])
        lead.append(mido.Message("program_change", program=65))
        lead.extend(note_track([64]))
        self.save([conductor, lead])
        analysis = analyze_midi(self.path)
        self.assertEqual(analysis.tempo_changes, ((0, 500000), (1, 750000)))
        self.assertEqual(analysis.time_signatures, ((0, 4, 4), (2, 3, 4)))
        self.assertEqual(analysis.selected.programs, (0, 65))
        self.assertEqual([n.program for n in analysis.melody], [0, 65])

    def test_register_shift_and_output_range_aliases(self):
        self.save([note_track([108, 112, 115, 112])])
        source = analyze_midi(self.path).as_melody()
        song = MidiSong(source, adapt_register=True)
        self.assertEqual(song.register_shift, -36)
        self.assertTrue(all(48 <= n.note <= 96 for n in song.melody))
        self.assertEqual([n.note for n in source.notes], [108, 112, 115, 112])
        self.assertEqual(MidiSong(source).register_shift, 0)
        args = parser().parse_args(["loopian", "--input", "x", "--output", "y", "--output-low", "55", "--output-high", "84"])
        self.assertEqual((args.note_min, args.note_max), (55, 84))

    def test_all_three_fixtures_flow_without_autonomous_progress(self):
        for name in ("loopian_type0.mid", "loopian_type1.mid", "loopian_gm.mid"):
            song = MidiSong(analyze_midi(EXAMPLES / name).as_melody(), adapt_register=True)
            port = Port(Clock())
            engine = LoopianEngine(port, song, Timing(grid=0), mode="melody-flow", seed=1)
            engine.tick(100)
            self.assertEqual(engine.melody_position, 0)
            self.assertFalse(port.messages)
            previous = None
            for i in range(50):
                key = 40 + i % 10
                engine.receive(mido.Message("note_on", note=key), i * 0.25)
                engine.tick(i * 0.25)
                engine.tick(i * 0.25 + 0.061)
                selection = engine.flow.last_selection
                note = selection.selected.note
                self.assertIn(note % 12, selection.context.allowed)
                if previous is not None and engine.pitch.boundary_reason is None:
                    self.assertGreater((note - previous) * (1 if i % 10 else -1), 0)
                previous = note
                engine.receive(mido.Message("note_on", note=key, velocity=0), i * 0.25 + 0.1)
                engine.tick(i * 0.25 + 0.161)
            self.assertGreater(engine.melody_position, len(song.melody))
            self.assertFalse(engine.sounding or engine.queue or engine.held)

    def test_flow_all_supported_qualities_and_sparse_palettes(self):
        source = analyze_midi(EXAMPLES / "loopian_gm.mid").as_melody()
        for chord in ("C", "Dm", "G7", "Ebmaj7", "Am7", "Bdim", "F#m7b5", "Dsus4"):
            song = MidiSong(source, chords=(chord,))
            for previous in range(48, 97):
                for direction, sign in (("UP", 1), ("DOWN", -1)):
                    pitch = PitchShaper()
                    pitch.previous, pitch.last_chord = previous, chord
                    selection = FlowSelector(seed=1).select(pitch, song, Gesture(60, sign, direction, 0.2), 1, {})
                    self.assertIn(selection.selected.note % 12, selection.context.allowed)
                    if selection.pitch.boundary_reason is None:
                        self.assertGreater(sign * (selection.selected.note - previous), 0)
                        self.assertLessEqual(abs(selection.selected.note - previous), 7)

    def test_source_accent_is_optional_small_and_clamped(self):
        song = MidiSong(analyze_midi(EXAMPLES / "loopian_gm.mid").as_melody())
        for amount in (0, 1):
            port = Port(Clock())
            engine = LoopianEngine(port, song, Timing(grid=0), mode="melody-direct", source_accent=amount)
            engine.receive(mido.Message("note_on", note=60, velocity=126), 0)
            engine.tick(0)
            self.assertTrue(120 <= port.messages[-1].velocity <= 127)
            if not amount:
                self.assertEqual(port.messages[-1].velocity, 126)

    def test_invalid_and_unterminated_notes_reported(self):
        with self.assertRaises(ValueError):
            analyze_midi(self.path)
        self.save([mido.MidiTrack([mido.Message("note_on", note=60), mido.MetaMessage("end_of_track", time=480)])])
        analysis = analyze_midi(self.path)
        self.assertEqual(analysis.melody[0].duration, 1)
        self.assertIn("unterminated", analysis.warnings[0])
        self.save([note_track([36], channel=9)])
        with self.assertRaisesRegex(ValueError, "percussion"):
            analyze_midi(self.path)


class AnalysisCliTests(unittest.TestCase):
    def test_analyze_cli_uses_no_hardware_and_reports_candidates_harmony(self):
        output = io.StringIO()
        with patch("sys.argv", ["autoaccomp", "analyze-midi", "--midi-file", str(EXAMPLES / "loopian_gm.mid")]), \
             patch("autoaccomp.midi_io.backend") as backend, redirect_stdout(output):
            main()
        backend.assert_not_called()
        for expected in ("Alto Sax", "Melody candidates:", "confidence: HIGH", "Bar 2 beat 1: Am7", "Estimated key: C major"):
            self.assertIn(expected, output.getvalue())

    def test_loopian_real_midi_auto_selection_summary_and_cleanup(self):
        clock = Clock()
        source, target = Port(clock), Port(clock)
        for i in range(6):
            source.events.extend(((0.01 + i * 0.2, mido.Message("note_on", note=30 + i)),
                                  (0.1 + i * 0.2, mido.Message("note_off", note=30 + i))))
        output = io.StringIO()
        argv = ["autoaccomp", "loopian", "--input", "Keyboard", "--output", "Synth", "--bars", "1",
                "--midi-file", str(EXAMPLES / "loopian_gm.mid"), "--loopian-mode", "melody-flow", "--debug-loopian", "--seed", "1"]
        with patch("sys.argv", argv), patch("time.perf_counter", clock.counter), patch("time.sleep", clock.sleep), \
             patch("autoaccomp.midi_io.backend", return_value=Backend(source, target)), redirect_stdout(output):
            main()
        self.assertTrue(source.closed and target.closed and target.reset_called and target.panic_called)
        self.assertEqual(sum(m.type == "note_on" for m in target.messages), 6)
        for expected in ("Selected melody: Track 1", "Harmony analysis:", "Original melody median:", "Applied register shift:", "Harmony source: automatic"):
            self.assertIn(expected, output.getvalue())


if __name__ == "__main__":
    unittest.main()
