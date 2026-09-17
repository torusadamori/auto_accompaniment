import unittest
import mido
from autoaccomp.follow import Follower
from autoaccomp.config import COMP_CHANNEL, BASS_CHANNEL
from autoaccomp.main import parser


class Output:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class DebugAccompTests(unittest.TestCase):
    def test_five_chords_recognition_active_and_sent_notes_in_both_modes(self):
        # Independent expected pitches, not values calculated by the generator.
        cases = [([60, 64, 67], "C", 36, {4, 7, 11}),
                 ([62, 65, 69], "Dm", 38, {5, 9, 0}),
                 ([64, 67, 71], "Em", 40, {7, 11, 2}),
                 ([65, 69, 72], "F", 41, {9, 0, 4}),
                 ([67, 71, 74], "G", 43, {11, 2, 6})]
        for debug in (False, True):
            with self.subTest(debug=debug):
                out, logs = Output(), []
                follower = Follower(out, report=logs.append, debug_accomp=debug)
                follower.tick(0, 0)
                for index, (pitches, symbol, root, jazz_pcs) in enumerate(cases):
                    for offset, pitch in enumerate(pitches):
                        now = index + 0.1 + offset * 0.02
                        follower.receive(mido.Message("note_on", note=pitch, velocity=80), now)
                        follower.tick(now * 2, now)
                        follower.receive(mido.Message("note_off", note=pitch), now + 0.01)
                    follower.tick(index * 2 + 0.4, index + 0.2)
                    self.assertEqual(follower.detected.symbol, symbol)
                    start, log_start = len(out.messages), len(logs)
                    follower.tick(index * 2 + 1, index + 0.5)
                    self.assertEqual(follower.current.symbol, symbol)
                    sent = [m for m in out.messages[start:] if m.type == "note_on"]
                    comp = [m.note for m in sent if m.channel == COMP_CHANNEL]
                    bass = [m.note for m in sent if m.channel == BASS_CHANNEL]
                    self.assertEqual(bass, [root])
                    self.assertIn(f"Detected chord: {symbol}", logs)
                    self.assertIn(f"Active accompaniment chord: {symbol}", logs[log_start:])
                    if debug:
                        self.assertEqual(comp, pitches)
                        self.assertIn(f"Comping notes: {comp}", logs[log_start:])
                        self.assertIn(f"Bass note: {root}", logs[log_start:])
                        self.assertEqual([m.velocity for m in sent if m.channel == COMP_CHANNEL], [78] * 3)
                    else:
                        self.assertEqual({n % 12 for n in comp}, jazz_pcs)
                        self.assertFalse(any(s.startswith(("Comping notes:", "Bass note:")) for s in logs))
                    # No duplicate logs or events between attacks.
                    count = len(logs)
                    follower.tick(index * 2 + 1.02, index + 0.51)
                    self.assertEqual(len(logs), count)

    def prepare(self, out, logs, **options):
        follower = Follower(out, report=logs.append, debug_accomp=True, **options)
        follower.tick(0, 0)
        for pitch in (60, 64, 67):
            follower.receive(mido.Message("note_on", note=pitch), 0.1)
        follower.tick(0.4, 0.2)
        return follower

    def test_dropped_attacks_are_not_reported_as_sent(self):
        out, logs = Output(), []
        follower = self.prepare(out, logs)
        follower.tick(1.2, 0.6)  # beyond scheduler's late-attack threshold
        self.assertEqual(follower.scheduler.skipped, 4)
        self.assertFalse(out.messages)
        self.assertFalse(any(s.startswith(("Comping notes:", "Bass note:")) for s in logs))

    def test_failed_send_is_not_reported(self):
        class BrokenOutput:
            def send(self, message):
                raise OSError("test output failure")
        logs = []
        follower = self.prepare(BrokenOutput(), logs)
        with self.assertRaises(OSError):
            follower.tick(1, 0.5)
        self.assertFalse(any(s.startswith(("Comping notes:", "Bass note:")) for s in logs))

    def test_muted_parts_are_not_sent_or_logged(self):
        for options, forbidden_channel, prefix in (({"no_comping": True}, COMP_CHANNEL, "Comping notes:"),
                                                   ({"no_bass": True}, BASS_CHANNEL, "Bass note:")):
            out, logs = Output(), []
            follower = self.prepare(out, logs, **options)
            follower.tick(1, 0.5)
            self.assertTrue(out.messages)
            self.assertFalse(any(m.channel == forbidden_channel for m in out.messages))
            self.assertFalse(any(s.startswith(prefix) for s in logs))

    def test_offbeat_comping_logs_actual_sent_notes(self):
        out, logs = Output(), []
        follower = self.prepare(out, logs)
        for beat in (1, 2, 3):
            follower.tick(beat, beat / 2)
        start, log_start = len(out.messages), len(logs)
        follower.tick(3.5, 1.75)
        pitches = [m.note for m in out.messages[start:] if m.type == "note_on"]
        self.assertEqual(len(pitches), 3)
        self.assertIn(f"Comping notes: {pitches}", logs[log_start:])
        self.assertFalse(any(s.startswith("Bass note:") for s in logs[log_start:]))

    def test_cli_debug_is_opt_in(self):
        base = ["follow", "--input", "0", "--output", "0"]
        self.assertFalse(parser().parse_args(base).debug_accomp)
        self.assertTrue(parser().parse_args(base + ["--debug-accomp"]).debug_accomp)


if __name__ == "__main__":
    unittest.main()
