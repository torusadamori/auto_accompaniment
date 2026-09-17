"""Deterministic musical rules and real CLI with fake clock/physical ports."""
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

import mido

from autoaccomp.loopian import (FixedSong, GestureAnalyzer, LoopianEngine, PALETTES,
                               PitchShaper, Timing)
from autoaccomp.main import main
from test_melody_cli import Backend, Clock, Port


class LoopianTests(unittest.TestCase):
    def phrase(self, inputs, chord="Cmaj7"):
        analyzer, shaper = GestureAnalyzer(), PitchShaper()
        context = FixedSong(chords=(chord,)).at(0)
        return [shaper.choose(analyzer.receive(n, i * 0.1), context) for i, n in enumerate(inputs)]

    def test_context_clock_and_authored_melody(self):
        song = FixedSong()
        self.assertEqual([song.at(t).chord for t in (0, 2, 4, 6, 8)],
                         ["Cmaj7", "Dm7", "G7", "Cmaj7", "Cmaj7"])
        c = song.at(2.25)
        self.assertEqual((c.bar, c.beat, c.next_chord, c.tempo, c.key),
                         (1, 0.5, "G7", 120, "C major"))
        self.assertEqual(c.melody_range, (60, 69))
        self.assertEqual(len(c.melody), 8)

    def test_input_is_contour_not_absolute_pitch(self):
        self.assertEqual(self.phrase([60, 62, 64, 65, 67]), [64, 67, 69, 71, 72])
        self.assertEqual(self.phrase([1, 2, 3, 4, 5]), self.phrase([100, 102, 106, 107, 127]))
        self.assertEqual(self.phrase([67, 65, 64, 62, 60]), [64, 62, 60, 59, 57])

    def test_gesture_interval_rhythm_and_same(self):
        analyzer = GestureAnalyzer()
        self.assertEqual(analyzer.receive(60, 0).direction, "START")
        gesture = analyzer.receive(64, 0.2)
        self.assertEqual((gesture.interval, gesture.direction, gesture.elapsed), (4, "UP", 0.2))
        self.assertEqual(analyzer.receive(62, 0.3).direction, "DOWN")
        self.assertEqual(analyzer.receive(62, 0.4).direction, "SAME")
        self.assertEqual(self.phrase([60, 60, 60]), [64, 64, 64])

    def test_every_previous_note_chord_direction_and_octave_fold(self):
        for chord, allowed in PALETTES.items():
            for previous in range(55, 85):
                for step in (-1, 1):
                    analyzer, shaper = GestureAnalyzer(), PitchShaper()
                    analyzer.receive(60, 0)
                    shaper.previous = previous
                    output = shaper.choose(analyzer.receive(60 + step, 0.1), FixedSong(chords=(chord,)).at(0))
                    self.assertIn(output % 12, allowed)
                    self.assertTrue(55 <= output <= 84)
                    self.assertNotEqual(output, previous)
                    directional = [n for n in range(55, 85) if n % 12 in allowed and (n - previous) * step > 0]
                    if directional:
                        self.assertEqual(output, min(directional, key=lambda n: abs(n - previous)))
        self.assertEqual(self.phrase(list(range(60, 90)))[-1] % 12 in PALETTES["Cmaj7"], True)

    def test_timing_is_causal_light_bounded_and_switchable(self):
        self.assertAlmostEqual(Timing().due(0.110, 120), 0.1175)
        self.assertEqual(Timing().due(0.130, 120), 0.130)
        self.assertEqual(Timing().due(0.050, 120), 0.050)
        self.assertAlmostEqual(Timing(grid=8).due(0.230, 120), 0.240)
        self.assertEqual(Timing(grid=0).due(0.110, 120), 0.110)
        self.assertEqual(Timing(strength=0).due(0.110, 120), 0.110)
        for tempo in (20, 120, 300):
            for i in range(2000):
                now = i / 1000
                delay = Timing().due(now, tempo) - now
                self.assertTrue(0 <= delay <= 0.015 + 1e-9)

    def test_invalid_configuration(self):
        for tempo in (0, 301, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                FixedSong(tempo=tempo)
        for kwargs in ({"chords": ()}, {"chords": ("A7",)}, {"output_range": (60, 65)},
                       {"output_range": (-1, 84)}, {"beats_per_bar": 3}):
            with self.assertRaises(ValueError):
                FixedSong(**kwargs)
        for kwargs in ({"grid": 4}, {"strength": -1}, {"strength": float("nan")}, {"window": 0.2}):
            with self.assertRaises(ValueError):
                Timing(**kwargs)

    def engine(self, timing=Timing(grid=0)):
        port = Port(Clock())
        return LoopianEngine(port, FixedSong(), timing), port

    def test_release_uses_transformed_note_and_velocity_zero(self):
        engine, port = self.engine()
        engine.receive(mido.Message("note_on", note=60, velocity=90, channel=3), 0)
        engine.tick(0)
        engine.receive(mido.Message("note_on", note=60, velocity=0, channel=3), 0.3)
        engine.tick(0.3)
        self.assertEqual([(m.type, m.note, m.channel) for m in port.messages],
                         [("note_on", 64, 0), ("note_off", 64, 0)])
        self.assertEqual(port.messages[0].velocity, 90)
        self.assertFalse(engine.sounding)

    def test_short_tap_delays_attack_and_release_equally(self):
        engine, port = self.engine(Timing())
        engine.receive(mido.Message("note_on", note=60), 0.110)
        engine.receive(mido.Message("note_off", note=60), 0.112)
        engine.tick(0.117)
        self.assertFalse(port.messages)
        engine.tick(0.118)
        self.assertEqual(port.messages[-1].type, "note_on")
        engine.tick(0.120)
        self.assertEqual(port.messages[-1].type, "note_off")

    def test_same_pitch_retrigger_old_release_does_not_cut_new_voice(self):
        engine, port = self.engine()
        for channel in (0, 1):
            engine.receive(mido.Message("note_on", note=60, channel=channel), channel * 0.1)
            engine.tick(channel * 0.1)
        self.assertEqual([m.type for m in port.messages], ["note_on", "note_off", "note_on"])
        engine.receive(mido.Message("note_off", note=60, channel=0), 0.2)
        engine.tick(0.2)
        self.assertEqual(len(port.messages), 3)
        engine.receive(mido.Message("note_off", note=60, channel=1), 0.3)
        engine.tick(0.3)
        self.assertFalse(engine.sounding)

    def test_duplicate_input_fifo_and_chord_change_release(self):
        engine, port = self.engine()
        engine.receive(mido.Message("note_on", note=60), 0)
        engine.tick(0)
        engine.receive(mido.Message("note_on", note=60), 2)
        engine.tick(2)
        for t in (2.1, 2.2):
            engine.receive(mido.Message("note_off", note=60), t)
            engine.tick(t)
        self.assertFalse(engine.held)
        self.assertFalse(engine.sounding)

    def test_harmony_at_actual_output_and_silent_reference_melody(self):
        engine, port = self.engine(Timing())
        engine.tick(1)
        self.assertFalse(port.messages)
        engine.pitch.previous = 64
        engine.gestures.receive(60, 1)
        engine.receive(mido.Message("note_on", note=62), 1.99)
        engine.tick(2.001)
        self.assertEqual(port.messages[-1].note, 65)  # Dm7 F, not Cmaj7 G

    def test_controls_cleanup_pending_cancellation_and_ignored_raw_pitch(self):
        engine, port = self.engine(Timing())
        engine.receive(mido.Message("pitchwheel", pitch=4000), 0)
        engine.receive(mido.Message("note_off", note=99), 0)
        self.assertFalse(port.messages)
        engine.receive(mido.Message("note_on", note=60), 0)
        engine.tick(0)
        engine.receive(mido.Message("control_change", control=64, value=127), 0.05)
        engine.receive(mido.Message("note_on", note=62), 0.110)
        engine.receive(mido.Message("control_change", control=123), 0.112)
        engine.tick(1)
        self.assertFalse(engine.queue or engine.held or engine.sounding)
        self.assertEqual(sum(m.type == "note_on" for m in port.messages), 1)
        self.assertEqual(port.messages[-2].value, 0)
        self.assertEqual(port.messages[-1].type, "note_off")

    def test_queue_overflow_is_explicit_and_can_be_cleaned_up(self):
        engine, _ = self.engine(Timing())
        engine.LIMIT = 2
        for note in (60, 62):
            engine.receive(mido.Message("note_on", note=note), 0.11)
        with self.assertRaisesRegex(RuntimeError, "limit"):
            engine.receive(mido.Message("note_on", note=64), 0.11)
        engine.close()
        self.assertFalse(engine.queue or engine.held or engine.sounding)


class LoopianCliTests(unittest.TestCase):
    def exercise(self, flags=(), interrupt=False):
        clock = Clock()
        source, target = Port(clock), Port(clock)
        for i, note in enumerate((60, 62, 64, 65, 67, 65, 64, 62)):
            source.events.extend([(i + 0.01, mido.Message("note_on", note=note, velocity=83)),
                                  (i + 0.31, mido.Message("note_off", note=note))])
        logs = io.StringIO()
        argv = ["autoaccomp", "loopian", "--input", "Keyboard", "--output", "Synth", "--bars", "4", *flags]

        def sleep(seconds):
            if interrupt and clock.now > 0.1:
                raise KeyboardInterrupt
            clock.sleep(seconds)

        with patch("sys.argv", argv), patch("time.perf_counter", clock.counter), \
             patch("time.sleep", sleep), patch("autoaccomp.midi_io.backend", return_value=Backend(source, target)), \
             redirect_stdout(logs):
            main()
        return source, target, logs.getvalue()

    def test_cli_progression_debug_and_cleanup(self):
        source, target, log = self.exercise(["--debug-loopian"])
        self.assertTrue(source.closed and target.closed and target.reset_called and target.panic_called)
        attacks = [m for m in target.messages if m.type == "note_on"]
        releases = [m for m in target.messages if m.type == "note_off"]
        self.assertEqual(len(attacks), 8)
        self.assertEqual([m.note for m in attacks], [64, 67, 69, 72, 74, 71, 69, 67])
        self.assertEqual([m.note for m in attacks], [m.note for m in releases])
        self.assertTrue(all(m.channel == 0 for m in target.messages))
        for text in ("Direction: START", "Interval: +2", "Direction: UP", "Direction: DOWN",
                     "Input note: 60", "Output note: 64 (E4)", "Bar 2: Dm7", "Bar 3: G7", "actual="):
            self.assertIn(text, log)

    def test_fixed_chord_and_no_debug(self):
        _, _, log = self.exercise(["--chords", "Cmaj7", "--grid", "0"])
        self.assertIn("Bar 4: Cmaj7 -> Cmaj7", log)
        self.assertNotIn("Input note:", log)

    def test_ctrl_c_releases_held_note_and_closes_ports(self):
        source, target, log = self.exercise(interrupt=True)
        self.assertTrue(source.closed and target.closed and target.reset_called and target.panic_called)
        self.assertEqual([m.type for m in target.messages if m.type.startswith("note_")],
                         ["note_on", "note_off"])
        self.assertIn("Stopped", log)


if __name__ == "__main__":
    unittest.main()
