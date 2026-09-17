import unittest
from unittest.mock import patch
import mido
from autoaccomp.chord_progression import parse_chord
from autoaccomp.harmony_estimator import HarmonyEstimator
from autoaccomp.progression_estimator import ProgressionEstimator
from autoaccomp.melody_history import MelodyNote
from autoaccomp.melody_follow import MelodyFollower
from autoaccomp.main import parser


def phrase(pitches, start=0):
    return [MelodyNote(pitch, 80, 0, (start+i)/2, start+i,
                       (start+i+0.8)/2, start+i+0.8) for i, pitch in enumerate(pitches)]


class Output:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class ProgressionTests(unittest.TestCase):
    def test_transition_scores_prefer_dm_g_and_g_c(self):
        estimator = ProgressionEstimator()
        estimator.current = parse_chord("Dm")
        parts = estimator.score_parts(phrase([71]), 1)
        self.assertEqual(parts["G"].melody, parts["Em"].melody)
        self.assertGreater(parts["G"].transition, parts["Em"].transition)
        chord, _ = estimator.estimate(phrase([71]), 1)
        self.assertEqual(chord.symbol, "G")
        parts = estimator.score_parts(phrase([60], start=4), 5)
        self.assertGreater(parts["C"].transition, parts["F"].transition)
        chord, _ = estimator.estimate(phrase([60], start=4), 5)
        self.assertEqual(chord.symbol, "C")

    def test_melody_fit_overrides_preferred_progression(self):
        estimator = ProgressionEstimator()
        estimator.current = parse_chord("Dm")
        chord, scores = estimator.estimate(phrase([64, 67, 71]), 3)
        self.assertEqual(chord.symbol, "Em")  # Dm->G is favored but E/G/B supports Em.
        self.assertGreater(scores["Em"], scores["G"])
        self.assertEqual(estimator.parts["Em"].transition, -0.15)

    def test_minimum_hold_and_no_information_during_rest(self):
        estimator = ProgressionEstimator()
        chord, _ = estimator.estimate(phrase([62, 65, 69]), 3)
        self.assertEqual(chord.symbol, "Dm")
        chord, _ = estimator.estimate(phrase([71], start=3.1), 4)
        self.assertEqual(chord.symbol, "Dm")
        with patch.object(estimator, "score_parts", wraps=estimator.score_parts) as scoring:
            for beat in (5, 6, 7, 8):
                chord, _ = estimator.estimate(phrase([71], start=3.1), beat)
                self.assertEqual(chord.symbol, "Dm")
                self.assertFalse(estimator.evaluated)
            self.assertEqual(scoring.call_count, 0)
        chord, _ = estimator.estimate(phrase([71], start=8.1), 9)
        self.assertEqual(chord.symbol, "G")

    def test_bar_position_favors_holding_midbar_and_change_at_downbeat(self):
        estimator = ProgressionEstimator()
        estimator.current = parse_chord("Dm")
        head = estimator.score_parts(phrase([71], start=3), 4)
        middle = estimator.score_parts(phrase([71], start=4), 5)
        self.assertEqual(head["G"].bar, 0.1)
        self.assertEqual(head["Dm"].bar, 0)
        self.assertEqual(middle["G"].bar, 0)
        self.assertEqual(middle["Dm"].bar, 0.2)
        self.assertGreater(head["G"].total-head["Dm"].total,
                           middle["G"].total-middle["Dm"].total)
        for part in head.values():
            self.assertAlmostEqual(part.total, part.melody+part.transition+part.hold+part.bar)

    def test_clear_melody_allows_midbar_change(self):
        estimator = ProgressionEstimator()
        estimator.current = parse_chord("C")
        estimator.last_change = 0
        chord, _ = estimator.estimate(phrase([62, 65, 69], start=2), 5)
        self.assertEqual(chord.symbol, "Dm")

    def test_same_ambiguous_evidence_changes_at_bar_head_but_holds_midbar(self):
        selected = []
        for shift in (0, 1):
            estimator = ProgressionEstimator()
            estimator.current = parse_chord("Dm")
            estimator.last_change = 0
            notes = [MelodyNote(65, 80, 0, 0, 0.5+shift, 0.5, 1.5+shift),
                     MelodyNote(67, 80, 0, 0.5, 1.5+shift, 1, 2.5+shift)]
            selected.append(estimator.estimate(notes, 4+shift)[0].symbol)
        self.assertEqual(selected, ["G", "Dm"])

    def test_downbeat_more_influential_than_third_beat(self):
        estimator = ProgressionEstimator()
        # A long scoring window minimizes recency differences to isolate accent.
        first = MelodyNote(60, 80, 0, 0, 0, 0.5, 1)
        third = MelodyNote(62, 80, 0, 1, 2, 1.5, 3)
        scores = estimator.score_parts([first, third], 3, window_beats=100)
        swapped = estimator.score_parts([MelodyNote(62, 80, 0, 0, 0, 0.5, 1),
                                         MelodyNote(60, 80, 0, 1, 2, 1.5, 3)], 3, window_beats=100)
        self.assertGreater(scores["C"].melody, swapped["C"].melody)

    def test_long_note_outweighs_short_passing_note(self):
        estimator = ProgressionEstimator()
        notes = [MelodyNote(60, 80, 0, 0, 0, 1, 2),
                 MelodyNote(62, 80, 0, 1, 2, 1.125, 2.25)]
        parts = estimator.score_parts(notes, 3)
        self.assertGreater(parts["C"].melody, parts["G"].melody)

    def test_rest_suppresses_debug_and_active_output_continues(self):
        out, logs = Output(), []
        follower = MelodyFollower(out, start=0, report=logs.append, debug_harmony=True, progression_aware=True)
        follower.tick(0, 0)
        self.assertFalse(logs)
        follower.receive(mido.Message("note_on", note=60), 0.1)
        follower.receive(mido.Message("note_off", note=60), 0.4)
        follower.tick(1, 0.5)
        self.assertEqual(follower.engine.current.symbol, "C")
        self.assertTrue(any("melody=" in line and "total=" in line for line in logs))
        count, sent = len(logs), len(out.messages)
        for beat in range(2, 12):
            follower.tick(beat, beat/2)
            self.assertEqual(follower.engine.current.symbol, "C")
        self.assertEqual(len(logs), count)
        self.assertGreater(len(out.messages), sent)

    def test_held_note_keeps_duration_evidence_fresh(self):
        estimator = ProgressionEstimator()
        held = [MelodyNote(60, 80, 0, 0, 0)]
        for beat in range(1, 7):
            estimator.estimate(held, beat)
            self.assertTrue(estimator.evaluated)

    def test_known_melody_comparison_and_output_follows_estimate(self):
        counts = []
        for aware in (False, True):
            out = Output()
            follower = MelodyFollower(out, start=0, report=lambda _: None, progression_aware=aware)
            follower.tick(0, 0)
            changes = []
            pitches = [60,60,67,67,69,69,67,65,65,64,64,62,62,60]
            roots = {"C":36, "Dm":38, "Em":40, "F":41, "G":43, "Am":45}
            for beat, pitch in enumerate(pitches):
                follower.receive(mido.Message("note_on", note=pitch), (beat+0.05)/2)
                follower.receive(mido.Message("note_off", note=pitch), (beat+0.9)/2)
                previous, sent = follower.engine.current, len(out.messages)
                follower.tick(beat+1, (beat+1)/2)
                current = follower.engine.current
                self.assertEqual(current, follower.estimator.current)
                if current != previous:
                    changes.append(beat+1)
                    attacks = [m for m in out.messages[sent:] if m.type == "note_on"]
                    self.assertEqual([m.note for m in attacks if m.channel == 2], [roots[current.symbol]])
                    self.assertEqual(len([m for m in attacks if m.channel == 1]), 3)
            self.assertTrue(all(b-a >= 2 for a,b in zip(changes, changes[1:])))
            counts.append(len(changes))
        self.assertLessEqual(counts[1], counts[0])

    def test_opt_in_preserves_third_mvp_estimator(self):
        self.assertIs(type(MelodyFollower(Output()).estimator), HarmonyEstimator)
        args = ["melody-follow", "--input", "0", "--output", "0"]
        self.assertFalse(parser().parse_args(args).progression_aware)
        self.assertTrue(parser().parse_args(args+["--progression-aware"]).progression_aware)


if __name__ == "__main__":
    unittest.main()
