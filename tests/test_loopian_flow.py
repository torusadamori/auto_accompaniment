"""FLOW invariants and seeded musical choices, including real MIDI queue integration."""
import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

import mido

from autoaccomp.loopian import (Gesture, LoopianEngine, MidiSong, PALETTES, PRIMARY,
                               PitchShaper, Timing, run_loopian)
from autoaccomp.loopian_flow import FlowSelector, gesture_strength, phrase_speed
from autoaccomp.main import main, parser
from autoaccomp.midi_melody import load_melody
from test_loopian_midi import DEMO
from test_melody_cli import Backend, Clock, Port


class FlowTests(unittest.TestCase):
    def song(self, **kwargs):
        return MidiSong(load_melody(DEMO), **kwargs)

    def engine(self, **kwargs):
        port = Port(Clock())
        return LoopianEngine(port, self.song(), Timing(grid=0), mode="melody-flow", **kwargs), port

    def perform(self, seed=1, amount=0.5, keys=None, spacing=0.25, mode="melody-flow"):
        port = Port(Clock())
        engine = LoopianEngine(port, self.song(), Timing(grid=0), mode=mode, seed=seed, flow_strength=amount)
        keys = keys if keys is not None else [60 + (i % 7) * 3 for i in range(60)]
        selections = []
        for i, key in enumerate(keys):
            now = i * spacing
            engine.receive(mido.Message("note_on", note=key, velocity=83), now)
            engine.tick(now)
            engine.tick(now + 0.061)
            engine.receive(mido.Message("note_off", note=key), now + 0.1)
            engine.tick(now + 0.161)
            if engine.flow_active:
                selections.append(engine.flow.last_selection)
        return engine, port, selections

    def test_classification_and_validation(self):
        for interval in range(-12, 13):
            expected = "SMALL" if abs(interval) <= 2 else "MEDIUM" if abs(interval) <= 5 else "LARGE"
            self.assertEqual(gesture_strength(interval), expected)
        self.assertEqual([phrase_speed(t) for t in (None, 0.1, 0.18, 0.3, 0.4)],
                         ["SLOW", "FAST", "NORMAL", "NORMAL", "SLOW"])
        for kwargs in ({"window": 0}, {"window": 9}, {"window": 1.5}, {"strength": -0.1},
                       {"strength": 1.1}, {"strength": float("nan")}, {"strength": float("inf")}):
            with self.assertRaises(ValueError):
                FlowSelector(**kwargs)

    def test_window_keeps_metadata_and_every_candidate_stays_inside(self):
        _, _, selections = self.perform()
        for choice in selections:
            for event in choice.window:
                self.assertLessEqual(abs(event.position - choice.cursor), 3)
                self.assertGreaterEqual(event.position, 0)
                self.assertIn(event.source.contour, ("UP", "DOWN", "SAME"))
                self.assertGreater(event.source.duration, 0)
                self.assertGreater(event.source.velocity, 0)
            positions = {event.position for event in choice.window}
            self.assertTrue(all(c.position in positions for c in choice.candidates))
            self.assertIn(choice.selected, choice.finalists)
            self.assertLessEqual(len(choice.finalists), 3)
            self.assertTrue(all(c.cost <= choice.candidates[0].cost + 0.75 + 1e-9 for c in choice.finalists))

    def test_cursor_monotonic_bounded_and_no_consecutive_holds(self):
        engine, _, selections = self.perform(amount=1, keys=[40 + (i % 8) * 7 for i in range(100)])
        advances = [c.next_position - c.cursor for c in selections]
        self.assertTrue(all(0 <= step <= 3 for step in advances))
        self.assertTrue(all(a or b for a, b in zip(advances, advances[1:])))
        self.assertGreater(engine.melody_position, 16)  # loops without resetting the absolute cursor
        self.assertTrue(any(step > 1 for step in advances))
        for choice in selections:
            # Search cannot silently skip a whole harmony/bar.
            self.assertEqual(self.song().context_for(choice.selected.position).bar, choice.context.bar)

    def test_zero_strength_exactly_matches_phase2_even_at_boundaries(self):
        keys = [40 + i for i in range(70)] + [110 - i for i in range(70)]
        for spacing in (0.2, 0.8):
            a, port_a, _ = self.perform(keys=keys, spacing=spacing, mode="melody-transform")
            b, port_b, _ = self.perform(keys=keys, spacing=spacing, amount=0)
            self.assertEqual(port_a.messages, port_b.messages)
            self.assertEqual(a.melody_position, b.melody_position)

    def test_seed_reproducible_and_different_seeds_vary(self):
        a, port_a, seq_a = self.perform(seed=1)
        b, port_b, seq_b = self.perform(seed=1)
        _, port_c, seq_c = self.perform(seed=2)
        self.assertEqual(port_a.messages, port_b.messages)
        self.assertEqual([c.selected.position for c in seq_a], [c.selected.position for c in seq_b])
        self.assertNotEqual(port_a.messages, port_c.messages)
        self.assertNotEqual([c.selected.position for c in seq_a], [c.selected.position for c in seq_c])
        self.assertFalse(a.sounding or b.sounding)

    def test_freedom_grows_with_amount_large_gestures_and_speed(self):
        song = self.song(chords=("Cmaj7",))
        def choices(amount, interval=7, elapsed=0.25):
            flow, pitch = FlowSelector(strength=amount, seed=1), PitchShaper()
            pitch.previous, pitch.last_chord = 64, "Cmaj7"
            return flow.select(pitch, song, Gesture(67, interval, "UP", elapsed), 1, PALETTES)
        small, large = choices(0.1), choices(1)
        self.assertLess(len(small.candidates), len(large.candidates))
        self.assertTrue({(c.position, c.note) for c in small.candidates}.issubset(
            {(c.position, c.note) for c in large.candidates}))
        self.assertGreater(max(c.note for c in choices(1).candidates), max(c.note for c in choices(1, 1).candidates))
        fast = choices(1, elapsed=0.1)
        self.assertTrue(all(c.position <= fast.cursor for c in fast.candidates))
        self.assertTrue(any(c.position > large.cursor for c in large.candidates))

    def test_direction_range_and_leaps_all_chords(self):
        for chord in PALETTES:
            song = self.song(chords=(chord,))
            for previous in range(48, 97):
                for sign, direction in ((1, "UP"), (-1, "DOWN")):
                    for size, maximum in ((1, 5), (7, 7)):
                        pitch = PitchShaper()
                        pitch.previous, pitch.last_chord = previous, chord
                        choice = FlowSelector(strength=1, seed=2).select(
                            pitch, song, Gesture(60, size * sign, direction, 0.25), 1, PALETTES)
                        for candidate in choice.candidates:
                            self.assertIn(candidate.note % 12, PALETTES[chord])
                            self.assertTrue(48 <= candidate.note <= 96)
                            if choice.pitch.boundary_reason is None:
                                self.assertGreater((candidate.note - previous) * sign, 0)
                                self.assertLessEqual(abs(candidate.note - previous), maximum)

    def test_primary_at_strong_beats_and_two_notes_after_change(self):
        song = self.song()
        flow, pitch = FlowSelector(seed=5), PitchShaper()
        pitch.previous, pitch.last_chord = 69, "Dm7"
        for cursor in (8, 9):
            choice = flow.select(pitch, song, Gesture(62, -2, "DOWN", 0.25), cursor, PALETTES)
            self.assertTrue(all(c.note % 12 in PRIMARY["G7"] for c in choice.candidates))
            pitch = choice.pitch
            flow.commit(choice, "DOWN")
        for cursor in (0, 2):
            pitch.previous, pitch.last_chord = 67, "Cmaj7"
            choice = flow.select(pitch, song, Gesture(62, 2, "UP", 0.25), cursor, PALETTES)
            self.assertTrue(all(c.note % 12 in PRIMARY["Cmaj7"] for c in choice.candidates))

    def test_fast_weak_notes_allow_secondary_slow_strengthens_primary(self):
        song, pitch = self.song(chords=("Cmaj7",)), PitchShaper()
        pitch.previous, pitch.last_chord = 67, "Cmaj7"
        choices = [FlowSelector(seed=1).select(pitch, song, Gesture(62, 2, "UP", speed), 3, PALETTES)
                   for speed in (0.1, 0.5)]
        # Compare the same source/pitch pair so other window choices don't mask the preference.
        def cost(choice, note):
            return next(c.cost for c in choice.candidates if c.position == 3 and c.note == note)
        self.assertLess(cost(choices[0], 69) - cost(choices[0], 71), cost(choices[1], 69) - cost(choices[1], 71))
        self.assertTrue(any(c.role == "SECONDARY" for c in choices[0].finalists))

    def test_repetition_suppression_changes_ranking_but_respects_human(self):
        flow = FlowSelector(strength=1, seed=1)
        flow.history.extend([(67, "UP", 0), (69, "UP", 1), (67, "UP", 2),
                             (69, "UP", 3), (67, "UP", 4)])
        self.assertEqual(flow.repetition_cost(69, "UP"), 3)
        self.assertEqual(flow.repetition_cost(69, "SAME"), 0)
        song, pitch = self.song(chords=("Cmaj7",)), PitchShaper()
        pitch.previous, pitch.last_chord = 67, "Cmaj7"
        normal = FlowSelector(strength=1, seed=1).select(pitch, song, Gesture(62, 2, "UP", 0.1), 3, PALETTES)
        suppressed = flow.select(pitch, song, Gesture(62, 2, "UP", 0.1), 3, PALETTES)
        self.assertEqual(normal.candidates[0].note, 69)
        self.assertNotEqual(suppressed.candidates[0].note, 69)
        flow.history.clear()
        flow.history.extend([(67, "DOWN", 0), (69, "UP", 1), (67, "DOWN", 2),
                             (69, "UP", 3), (67, "DOWN", 4)])
        self.assertEqual(flow.repetition_cost(69, "UP"), 0.8)

    def test_short_motif_only_rewards_agreement(self):
        flow, song = FlowSelector(strength=1), self.song()
        history = [(64, "UP", 1), (67, "UP", 2)]
        self.assertGreater(flow.motif_bonus(song, 3, "UP", history), 0)
        self.assertEqual(flow.motif_bonus(song, 3, "DOWN", history), 0)

    def test_same_holds_and_memory_stays_bounded(self):
        engine, port, _ = self.perform(keys=[60] * 40)
        notes = [m.note for m in port.messages if m.type == "note_on"]
        self.assertTrue(all(a == b for a, b in zip(notes[:3], notes[1:4])))
        self.assertEqual(len(engine.flow.history), 8)
        self.assertTrue(all(d in ("SAME", "START") for _, d, _ in engine.flow.history))

    def test_phrase_gap_continues_cursor_resets_short_memory(self):
        engine, _, _ = self.perform(keys=[60, 62, 64])
        cursor = engine.melody_position
        old_history = tuple(engine.flow.history)
        engine.receive(mido.Message("note_on", note=65), 2)
        engine.tick(2)
        self.assertEqual(engine.pitch.boundary_reason, "INPUT_PAUSE")
        self.assertEqual(engine.flow.last_selection.selected.position, cursor)
        self.assertEqual(engine.melody_position, cursor + 1)
        self.assertEqual(len(engine.flow.history), 1)
        self.assertEqual(engine.flow.previous_phrase, tuple(n for n, _, _ in old_history))

    def test_range_gap_preview_does_not_consume_rng_cursor_or_history(self):
        engine, port = self.engine(seed=1)
        engine.pitch.previous, engine.pitch.last_chord = 96, "Cmaj7"
        engine.gestures.receive(60, 0)
        before = engine.flow.rng.getstate()
        engine.receive(mido.Message("note_on", note=61), 0.1)
        engine.tick(0.1)
        self.assertEqual(engine.flow.rng.getstate(), before)
        self.assertEqual(engine.melody_position, 0)
        self.assertFalse(engine.flow.history)
        engine.receive(mido.Message("note_on", note=61, velocity=0), 0.105)
        engine.receive(mido.Message("note_on", note=62), 0.11)
        engine.receive(mido.Message("note_off", note=62), 0.115)
        engine.tick(0.17)
        self.assertEqual(sum(m.type == "note_on" for m in port.messages), 2)
        self.assertEqual(len(engine.flow.history), 2)
        cursor = engine.melody_position
        engine.tick(0.3)
        self.assertEqual(engine.melody_position, cursor)
        self.assertFalse(engine.sounding or engine.held or engine.queue)

    def test_reset_rewinds_cursor_memory_and_rng(self):
        engine, _, _ = self.perform()
        engine.receive(mido.Message("control_change", control=123), 20)
        self.assertEqual(engine.melody_position, 0)
        self.assertFalse(engine.flow.history or engine.flow.previous_phrase)
        self.assertEqual(engine.flow.rng.getstate(), FlowSelector(seed=1).rng.getstate())
        self.assertFalse(engine.held or engine.sounding or engine.queue)

    def test_already_queued_attacks_keep_order_after_a_late_boundary_tick(self):
        engine, port = self.engine(seed=1)
        engine.pitch.previous = 96
        engine.gestures.receive(60, 0)
        engine.receive(mido.Message("note_on", note=61, velocity=70), 0.1)
        engine.receive(mido.Message("note_on", note=62, velocity=90), 0.11)
        engine.tick(0.105)  # Starts the gap, while the second attack is already queued.
        engine.receive(mido.Message("note_on", note=63, velocity=80), 0.12)
        engine.tick(0.2)  # Late enough to process both the old due time and the rebase.
        attacks = [m for m in port.messages if m.type == "note_on"]
        self.assertEqual([m.velocity for m in attacks], [70, 90, 80])
        self.assertEqual(len(engine.flow.history), 3)
        self.assertGreater(attacks[1].note, attacks[0].note)
        self.assertGreater(attacks[2].note, attacks[1].note)


class FlowCliTests(unittest.TestCase):
    def test_real_cli_starts_and_reports_flow_candidates(self):
        clock = Clock()
        source, target = Port(clock), Port(clock)
        for i, note in enumerate((30, 32, 39, 38, 38, 41)):
            source.events.extend(((i * 0.25 + 0.01, mido.Message("note_on", note=note)),
                                  (i * 0.25 + 0.1, mido.Message("note_on", note=note, velocity=0))))
        argv = ["autoaccomp", "loopian", "--input", "Keyboard", "--output", "Synth", "--bars", "1",
                "--midi-file", str(DEMO), "--loopian-mode", "melody-flow", "--seed", "1", "--debug-loopian"]
        logs = io.StringIO()
        with patch("sys.argv", argv), patch("time.perf_counter", clock.counter), \
             patch("time.sleep", clock.sleep), patch("autoaccomp.midi_io.backend", return_value=Backend(source, target)), \
             redirect_stdout(logs):
            main()
        self.assertTrue(source.closed and target.closed and target.reset_called and target.panic_called)
        self.assertEqual(sum(m.type == "note_on" for m in target.messages), 6)
        self.assertEqual(sum(m.type == "note_off" for m in target.messages), 6)
        for text in ("melody-flow", "Window:", "Candidates", "Selected:", "cost=", "seed=1",
                     "Gesture strength: LARGE", "Phrase speed:", "contour agreement=", "next cursor="):
            self.assertIn(text, logs.getvalue())

    def test_bad_flow_parameters_rejected_before_opening_ports(self):
        base = ["loopian", "--input", "Keyboard", "--output", "Synth", "--loopian-mode", "melody-flow"]
        for flags in ([], ["--midi-file", str(DEMO), "--flow-window", "0"],
                      ["--midi-file", str(DEMO), "--flow-strength", "nan"],
                      ["--midi-file", str(DEMO), "--flow-strength", "1.1"]):
            with patch("autoaccomp.loopian.output_port") as port, self.assertRaises(ValueError):
                run_loopian(parser().parse_args(base + flags))
            port.assert_not_called()


if __name__ == "__main__":
    unittest.main()
