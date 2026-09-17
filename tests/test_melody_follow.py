import unittest
import mido
from autoaccomp.melody_history import MelodyHistory, MelodyNote
from autoaccomp.harmony_estimator import HarmonyEstimator
from autoaccomp.melody_follow import MelodyFollower
from autoaccomp.midi_io import forward_pending
from autoaccomp.main import parser
from autoaccomp.config import COMP_CHANNEL, BASS_CHANNEL


class Output:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def phrase(pitches, start=0, duration=0.8):
    return [MelodyNote(pitch, 80, 0, (start+i)/2, start+i,
                       (start+i+duration)/2, start+i+duration) for i, pitch in enumerate(pitches)]


class MelodyTests(unittest.TestCase):
    def test_chord_tones_win_over_other_candidates(self):
        estimator = HarmonyEstimator()
        for pitches, expected in (([60, 64, 67], "C"), ([62, 65, 69], "Dm"),
                                  ([64, 67, 71], "Em"), ([65, 69, 72], "F"),
                                  ([67, 71, 74], "G"), ([69, 72, 76], "Am")):
            scores = estimator.score(phrase(pitches), 3)
            self.assertEqual(set(scores), {"C", "Dm", "Em", "F", "G", "Am"})
            self.assertEqual(max(scores, key=scores.get), expected)

    def test_long_notes_and_strong_beats_have_more_influence(self):
        estimator = HarmonyEstimator()
        short_c = MelodyNote(60, 80, 0, 0, 0, 0.1, 0.2)
        long_f = MelodyNote(65, 80, 0, 0.5, 1, 1.5, 3)
        scores = estimator.score([short_c, long_f], 3)
        self.assertGreater(scores["F"], scores["C"])
        strong = estimator.score(phrase([60, 62], start=0), 2)
        weak = estimator.score(phrase([62, 60], start=0), 2)
        self.assertGreater(strong["C"], weak["C"])

    def test_hysteresis_minimum_hold_and_change_margin(self):
        estimator = HarmonyEstimator()
        current, _ = estimator.estimate(phrase([60, 64, 67]), 3)
        self.assertEqual(current.symbol, "C")
        competing = phrase([62, 65, 69], start=2)
        current, _ = estimator.estimate(competing, 4.9)
        self.assertEqual(current.symbol, "C")
        current, _ = estimator.estimate(competing, 5)
        self.assertEqual(current.symbol, "Dm")
        # F is shared by Dm and F: continuity keeps Dm despite the tie.
        current, _ = estimator.estimate(phrase([65], start=7), 8)
        self.assertEqual(current.symbol, "Dm")

    def test_empty_history_and_chromatic_only_do_not_drive_changes(self):
        estimator = HarmonyEstimator()
        self.assertIsNone(estimator.estimate([], 0)[0])
        self.assertIsNone(estimator.estimate(phrase([61, 63, 66]), 3)[0])
        estimator.estimate(phrase([60, 64, 67]), 3)
        self.assertEqual(estimator.estimate([], 20)[0].symbol, "C")
        self.assertEqual(estimator.estimate(phrase([61], start=20), 21)[0].symbol, "C")

    def test_history_records_metadata_velocity_zero_and_bounded_window(self):
        history = MelodyHistory()
        history.receive(mido.Message("note_on", note=60, velocity=93), 10, 0)
        note = history.active
        self.assertEqual((note.note, note.pitch_class, note.velocity, note.onset_time, note.onset_beat),
                         (60, 0, 93, 10, 0))
        history.receive(mido.Message("note_on", note=60, velocity=0), 10.5, 1)
        self.assertEqual(note.duration, 0.5)
        self.assertEqual(note.end_beat, 1)
        self.assertEqual(history.recent(4), (note,))
        self.assertFalse(history.recent(5))
        for i in range(300):
            history.receive(mido.Message("note_on", note=60), i, i/100)
        self.assertEqual(len(history.notes), 256)

    def test_long_hold_remains_evidence_and_legato_off_does_not_end_new_note(self):
        history = MelodyHistory()
        history.receive(mido.Message("note_on", note=60), 0, 0)
        self.assertEqual(len(history.recent(20)), 1)
        history.receive(mido.Message("note_on", note=62), 10, 20)
        history.receive(mido.Message("note_off", note=60), 10.1, 20.2)
        self.assertEqual(history.active.note, 62)
        history.receive(mido.Message("control_change", control=123), 11, 22)
        self.assertIsNone(history.active)

    def test_thru_is_immediate_and_accompaniment_starts_on_next_beat(self):
        output = Output()
        follower = MelodyFollower(output, start=0, report=lambda _: None)
        follower.tick(0, 0)
        message = mido.Message("note_on", note=60, velocity=91)
        iterator = iter([message])
        class Source:
            def poll(self):
                return next(iterator, None)
        forward_pending(Source(), output, on_message=lambda msg: follower.receive(msg, 0.1))
        self.assertEqual(output.messages, [message])
        follower.tick(0.2, 0.1)
        self.assertIsNone(follower.engine.current)
        follower.tick(1, 0.5)
        self.assertEqual(follower.engine.current.symbol, "C")
        attacks = [m for m in output.messages[1:] if m.type == "note_on"]
        self.assertEqual([m.note for m in attacks if m.channel == BASS_CHANNEL], [36])
        self.assertEqual({m.note % 12 for m in attacks if m.channel == COMP_CHANNEL}, {4, 7, 11})

    def test_estimates_can_change_and_both_parts_follow(self):
        out = Output()
        follower = MelodyFollower(out, start=0, report=lambda _: None)
        follower.tick(0, 0)
        roots = {"C": 36, "Dm": 38, "Em": 40, "F": 41, "G": 43, "Am": 45}
        seen = []
        for step, pitch in enumerate([60, 64, 67, 60] + [62, 65, 69, 62] * 3):
            follower.receive(mido.Message("note_on", note=pitch), (step+0.1)/2)
            follower.receive(mido.Message("note_off", note=pitch), (step+0.9)/2)
            count = len(out.messages)
            previous = follower.engine.current
            follower.tick(step+1, (step+1)/2)
            current = follower.engine.current
            if current != previous:
                seen.append(current.symbol)
                attacks = [m for m in out.messages[count:] if m.type == "note_on"]
                self.assertEqual([m.note for m in attacks if m.channel == BASS_CHANNEL], [roots[current.symbol]])
                self.assertEqual(len([m for m in attacks if m.channel == COMP_CHANNEL]), 3)
        self.assertEqual(seen[0], "C")
        self.assertIn("Dm", seen)

    def test_known_melody_stays_in_candidates_and_changes_at_most_every_two_beats(self):
        out, logs = Output(), []
        follower = MelodyFollower(out, start=0, report=logs.append, debug_harmony=True)
        follower.tick(0, 0)
        changes = []
        pitches = [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]
        for beat, pitch in enumerate(pitches):
            follower.receive(mido.Message("note_on", note=pitch), (beat+0.05)/2)
            follower.receive(mido.Message("note_off", note=pitch), (beat+0.9)/2)
            old = follower.engine.current
            follower.tick(beat+1, (beat+1)/2)
            current = follower.engine.current
            self.assertIn(current.symbol, {"C", "Dm", "Em", "F", "G", "Am"})
            if current != old:
                changes.append(beat+1)
        self.assertTrue(changes)
        self.assertTrue(all(b-a >= 2 for a,b in zip(changes, changes[1:])))
        self.assertTrue(any(line.startswith("Candidates:") for line in logs))
        self.assertTrue(any(line.startswith("Estimated chord:") for line in logs))
        count = len(logs)
        follower.tick(14.1, 7.05)
        self.assertEqual(len(logs), count)

    def test_new_input_just_after_boundary_waits_for_following_boundary(self):
        follower = MelodyFollower(Output(), start=0, report=lambda _: None)
        follower.tick(0, 0)
        follower.receive(mido.Message("note_on", note=65), 0.501)
        follower.tick(1.002, 0.501)
        self.assertIsNone(follower.engine.current)
        follower.tick(2, 1)
        self.assertIsNotNone(follower.engine.current)

    def test_cli_defaults_and_unsupported_key(self):
        args = parser().parse_args(["melody-follow", "--input", "0", "--output", "0", "--debug-harmony"])
        self.assertEqual(args.key, "C")
        self.assertTrue(args.debug_harmony)
        with self.assertRaises(ValueError):
            HarmonyEstimator("G")


if __name__ == "__main__":
    unittest.main()
