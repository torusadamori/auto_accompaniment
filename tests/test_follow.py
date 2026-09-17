import unittest
import mido
from autoaccomp.chord_detection import HeldNotes, detect, NAMES, SUPPORTED
from autoaccomp.chord_progression import parse_chord
from autoaccomp import comping
from autoaccomp.config import BASS_CHANNEL, COMP_CHANNEL, MELODY_CHANNEL
from autoaccomp.follow import Follower, run_follow
from autoaccomp.main import parser
from autoaccomp.midi_io import forward_pending


class Output:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.output = Output()
        self.labels = []
        self.follower = Follower(self.output, report=self.labels.append)

    def keys(self, pitches):
        for channel, pitch in list(self.follower.notes.keys):
            self.follower.notes.receive(mido.Message("note_off", channel=channel, note=pitch))
        for pitch in pitches:
            self.follower.notes.receive(mido.Message("note_on", note=pitch, velocity=85))

    def tick(self, beat):
        self.follower.tick(beat, beat / 2)  # 120 BPM

    def test_every_root_quality_and_inversion(self):
        for root in NAMES:
            for quality in SUPPORTED:
                chord = parse_chord(root + quality)
                for inversion in range(len(chord.intervals)):
                    notes = HeldNotes()
                    pitches = [60 + chord.root + interval + (12 if i < inversion else 0)
                               for i, interval in enumerate(chord.intervals)]
                    for pitch in pitches + [pitches[0] + 12]:
                        notes.receive(mido.Message("note_on", note=pitch))
                    self.assertEqual(detect(notes.pitch_classes), chord)

    def test_held_keys_velocity_zero_channels_and_pedal(self):
        held = HeldNotes()
        for channel in (0, 1):
            held.receive(mido.Message("note_on", channel=channel, note=60))
        held.receive(mido.Message("note_on", channel=0, note=60))  # idempotent duplicate
        held.receive(mido.Message("note_on", channel=0, note=60, velocity=0))
        self.assertEqual(held.keys, {(1, 60)})
        held.receive(mido.Message("control_change", channel=1, control=64, value=127))
        held.receive(mido.Message("note_off", channel=1, note=60))
        self.assertFalse(held.keys)  # pedal does not mean physically pressed
        for control in (120, 123):
            held.receive(mido.Message("note_on", channel=1, note=64))
            held.receive(mido.Message("control_change", channel=1, control=control))
            self.assertFalse(held.keys)
        held.receive(mido.Message("note_on", note=67))
        held.receive(mido.Message("reset"))
        self.assertFalse(held.keys)

    def test_no_guess_for_single_notes_or_extra_tensions(self):
        for pitches in ([], [0], [0, 4], [0, 4, 7, 2], [0, 1, 4, 7]):
            self.assertIsNone(detect(pitches))

    def test_input_sequence_changes_both_parts_on_next_beat(self):
        self.tick(0)
        sequence = [([60, 64, 67], "C", 36), ([57, 60, 64], "Am", 45),
                    ([62, 65, 69], "Dm", 38), ([55, 59, 62, 65], "G7", 43)]
        for index, (pitches, symbol, bass_root) in enumerate(sequence):
            self.keys(pitches)
            self.tick(index + 0.2)
            count = len(self.output.messages)
            self.tick(index + 0.3)
            self.assertEqual(self.follower.detected.symbol, symbol)
            self.assertFalse(any(m.type == "note_on" for m in self.output.messages[count:]))
            count = len(self.output.messages)
            self.tick(index + 1.0)
            self.assertEqual(self.follower.current.symbol, symbol)
            attacks = [m for m in self.output.messages[count:] if m.type == "note_on"]
            self.assertEqual([m.note for m in attacks if m.channel == BASS_CHANNEL], [bass_root])
            voices = [m.note for m in attacks if m.channel == COMP_CHANNEL]
            self.assertEqual(len(voices), 3)
            self.assertEqual(set(voices), set(self.follower.previous))
        self.assertEqual(self.labels, [f"Detected chord: {s}" for _, s, _ in sequence])

    def test_staggered_press_and_repeated_chord_do_not_spam(self):
        for beat, pitches in ((0, [60]), (0.02, [60, 64]), (0.04, [60, 64, 67])):
            self.keys(pitches)
            self.tick(beat)
        self.tick(0.14)
        for beat in (0.2, 0.5, 1, 1.2):
            self.tick(beat)
        self.keys([64, 67, 72])
        self.tick(1.5)
        self.assertEqual(self.labels, ["Detected chord: C"])

    def test_release_and_unknown_stop_and_cancel_pending_syncopation(self):
        for pitches in ([], [60, 61, 64, 67]):
            self.setUp()
            self.keys([60, 64, 67])
            for beat in (0, 0.1, 1, 2, 3):
                self.tick(beat)
            self.assertTrue(self.follower.scheduler.queue)
            self.keys(pitches)
            self.tick(3.7)
            self.tick(3.8)
            self.tick(4)
            self.assertIsNone(self.follower.current)
            self.assertFalse(self.follower.scheduler.queue)
            self.assertFalse(self.follower.scheduler.active)
            count = len(self.output.messages)
            self.tick(5)
            self.assertEqual(len(self.output.messages), count)

    def test_new_chord_cancels_old_pending_offbeat_without_touching_thru(self):
        self.keys([60, 64, 67])
        for beat in (0, 0.1, 1, 2, 3):
            self.tick(beat)
        # Simulate late boundary servicing with an old syncopation still queued.
        self.keys([57, 60, 64])
        self.tick(3.1)
        self.tick(3.2)
        count = len(self.output.messages)
        self.tick(4)
        for msg in self.output.messages[count:]:
            self.assertNotEqual(msg.channel, MELODY_CHANNEL)
        self.assertEqual(self.follower.current.symbol, "Am")
        self.assertTrue(all(event[0] >= 4 for event in self.follower.scheduler.queue))

    def test_thru_and_recognition_share_the_same_input_batch(self):
        messages = [mido.Message("note_on", note=n, velocity=84) for n in (60, 64, 67)]
        iterator = iter(messages)
        class Source:
            def poll(self):
                return next(iterator, None)
        forward_pending(Source(), self.output, on_message=self.follower.notes.receive)
        self.assertEqual(self.output.messages, messages)
        self.assertEqual(detect(self.follower.notes.pitch_classes).symbol, "C")

    def test_triads_use_seventh_coloring(self):
        for symbol, extended in (("C", "Cmaj7"), ("Am", "Am7")):
            self.assertEqual(comping.voicing(parse_chord(symbol)), comping.voicing(parse_chord(extended)))

    def test_clock_stall_does_not_burst_and_queue_stays_bounded(self):
        self.keys([60, 64, 67])
        self.tick(0)
        self.tick(0.1)
        self.tick(1)
        count = len(self.output.messages)
        self.tick(20.7)
        self.assertFalse(any(m.type == "note_on" for m in self.output.messages[count:]))
        self.assertFalse(self.follower.scheduler.active)
        for step in range(2071, 10000):
            self.tick(step / 100)
            self.assertLessEqual(len(self.follower.scheduler.queue), 10)

    def test_follow_clock_releases_on_keyboard_interrupt(self):
        self.keys([60, 64, 67])
        for beat in (0, 0.1, 1):
            self.tick(beat)
        def interrupt():
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            run_follow(self.follower, 120, 0, interrupt)
        self.assertFalse(self.follower.scheduler.active)
        self.assertFalse(self.follower.scheduler.queue)

    def test_cli_modes_are_separate(self):
        self.assertEqual(parser().parse_args(["play", "--output", "0"]).chords,
                         ("Cmaj7", "A7", "Dm7", "G7"))
        args = parser().parse_args(["follow", "--input", "0", "--output", "0"])
        self.assertFalse(hasattr(args, "chords"))


if __name__ == "__main__":
    unittest.main()
