import unittest
from unittest.mock import patch, MagicMock
import mido
from autoaccomp.chord_progression import parse_chord, progression
from autoaccomp import comping, walking_bass
from autoaccomp.events import Note
from autoaccomp.scheduler import Scheduler
from autoaccomp.midi_io import forward_pending, output_port
from autoaccomp.transport import run


class Output:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class MusicTests(unittest.TestCase):
    def test_all_supported_chords(self):
        for root in ("C", "C#", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"):
            for quality in ("maj7", "m7", "7", "m7b5"):
                chord = parse_chord(root + quality)
                voices = comping.voicing(chord)
                self.assertEqual({n % 12 for n in voices}, set(chord.pitch_classes[1:]))
                self.assertLessEqual(max(voices) - min(voices), 12)
                self.assertTrue(all(52 <= n <= 72 for n in voices))
                for following in progression():
                    bass = walking_bass.generate(chord, following)
                    self.assertEqual([n.beat for n in bass], [0, 1, 2, 3])
                    self.assertEqual(bass[0].pitch % 12, chord.root)
                    self.assertTrue(all(35 <= n.pitch <= 55 for n in bass))
                    self.assertEqual(abs(bass[-1].pitch - walking_bass.root_note(following)), 1)

    def test_progression_and_rejection(self):
        self.assertEqual([c.symbol for c in progression()], ["Cmaj7", "A7", "Dm7", "G7"])
        for bad in ("C", "H7", "Am", "Cmaj9"):
            with self.assertRaises(ValueError):
                parse_chord(bad)
        with self.assertRaises(ValueError):
            progression([])

    def test_note_lifetimes_over_loop_boundary(self):
        output = Output()
        scheduler = Scheduler(output)
        chords = progression()
        previous = None
        for bar in range(8):
            chord, following = chords[bar % 4], chords[(bar + 1) % 4]
            events, previous = comping.generate(chord, bar, previous)
            scheduler.add(events + walking_bass.generate(chord, following), bar * 4)
        for tick in range(3201):
            scheduler.tick(tick / 100)
        active = set()
        for msg in output.messages:
            key = (msg.channel, msg.note)
            if msg.type == "note_on":
                self.assertNotIn(key, active)
                active.add(key)
            else:
                self.assertIn(key, active)
                active.remove(key)
        self.assertFalse(active)
        self.assertFalse(scheduler.queue)
        self.assertEqual(scheduler.skipped, 0)
        self.assertEqual(len(output.messages), 160)

    def test_stall_drops_attacks_but_releases_held_notes(self):
        output = Output()
        scheduler = Scheduler(output)
        scheduler.add([Note(0, 0.9, 40, 70, 2), Note(1, 0.9, 44, 70, 2)], 0)
        scheduler.tick(0)
        scheduler.tick(3)
        self.assertEqual([m.type for m in output.messages], ["note_on", "note_off"])
        self.assertFalse(scheduler.active)
        self.assertEqual(scheduler.skipped, 1)

    def test_thru_preserves_notes_and_velocity_isolates_channel(self):
        messages = [mido.Message("note_on", channel=2, note=60, velocity=91),
                    mido.Message("note_on", channel=2, note=60, velocity=0),
                    mido.Message("note_off", channel=2, note=62, velocity=15),
                    mido.Message("control_change", channel=2, control=64, value=127),
                    mido.Message("clock"), mido.Message("program_change", program=5)]
        iterator = iter(messages)
        class Source:
            def poll(self):
                return next(iterator, None)
        output = Output()
        forward_pending(Source(), output)
        self.assertEqual(output.messages, [m.copy(channel=0) for m in messages[:4]])

    def test_transport_uses_absolute_clock_and_skips_old_bars(self):
        bars = []
        # perf_counter: epoch, bar0, bar1, a stall to bar3, end of 4 bars
        with patch("autoaccomp.transport.time.perf_counter", side_effect=[10, 10, 12, 16.1, 18]), \
             patch("autoaccomp.transport.time.sleep"):
            run(progression(), 120, 4, lambda bar, chord, nxt: bars.append(bar))
        self.assertEqual(bars, [0, 1, 3])

    def test_output_releases_notes_on_interrupt(self):
        api = MagicMock()
        api.get_output_names.return_value = ["Synth"]
        port = api.open_output.return_value.__enter__.return_value
        with patch("autoaccomp.midi_io.backend", return_value=api):
            with self.assertRaises(KeyboardInterrupt):
                with output_port("Synth"):
                    raise KeyboardInterrupt
        port.reset.assert_called_once()
        port.panic.assert_called_once()
        api.open_output.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
