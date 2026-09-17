"""CLI integration: only time and physical port boundaries are replaced."""
import ast
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch
import mido
from autoaccomp.main import main
from autoaccomp.output_diagnostics import AuditedOutput


class Clock:
    now = 0.0

    def counter(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Port:
    def __init__(self, clock):
        self.clock = clock
        self.messages = []
        self.events = []
        self.reset_called = self.panic_called = self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def poll(self):
        if self.events and self.events[0][0] <= self.clock.now:
            return self.events.pop(0)[1]
        return None

    def send(self, message):
        self.messages.append(message)

    def reset(self):
        self.reset_called = True

    def panic(self):
        self.panic_called = True


class Backend:
    def __init__(self, source, target):
        self.source, self.target = source, target

    def get_input_names(self):
        return ["Keyboard"]

    def get_output_names(self):
        return ["Synth"]

    def open_input(self, name):
        assert name == "Keyboard"
        return self.source

    def open_output(self, name):
        assert name == "Synth"
        return self.target


class MelodyCliTests(unittest.TestCase):
    def exercise(self, flags):
        clock = Clock()
        source, target = Port(clock), Port(clock)
        pitches = [60,60,67,67,69,69,67,65,65,64,64,62,62,60]
        for i, pitch in enumerate(pitches):
            source.events.append((i*0.5+0.025, mido.Message("note_on", note=pitch, velocity=83)))
            source.events.append((i*0.5+0.45, mido.Message("note_off", note=pitch)))
        expected_thru = [msg for _, msg in source.events]
        argv = ["autoaccomp", "melody-follow", "--input", "Keyboard", "--output", "Synth", "--bars", "4"] + flags
        logs = io.StringIO()
        # Real main -> ports -> forward_pending -> history -> estimator -> engine -> scheduler -> send.
        with patch("sys.argv", argv), patch("time.perf_counter", clock.counter), \
             patch("time.sleep", clock.sleep), patch("autoaccomp.midi_io.backend", return_value=Backend(source, target)), \
             redirect_stdout(logs):
            main()
        return source, target, expected_thru, logs.getvalue()

    def test_cli_both_estimators_send_all_parts_and_logs_match_successful_sends(self):
        for flags in ([], ["--progression-aware"]):
            with self.subTest(flags=flags):
                source, target, expected, log = self.exercise(flags + ["--debug-harmony", "--debug-accomp"])
                self.assertTrue(source.closed and target.closed and target.reset_called and target.panic_called)
                thru = [m for m in target.messages if m.channel == 0 and m.type in ("note_on", "note_off")]
                self.assertEqual(thru, expected)
                attacks = {channel: [m.note for m in target.messages if m.type == "note_on" and m.channel == channel]
                           for channel in (0, 1, 2)}
                self.assertEqual(len(attacks[0]), 14)
                self.assertGreater(len(attacks[1]), 0)
                self.assertGreater(len(attacks[2]), 0)
                self.assertTrue(all(35 <= pitch <= 55 for pitch in attacks[2]))
                logged_comp, logged_bass = [], []
                for line in log.splitlines():
                    if line.startswith("Comping notes: "):
                        logged_comp.extend(ast.literal_eval(line.split(": ", 1)[1]))
                    if line.startswith("Bass note: "):
                        logged_bass.append(int(line.split(": ", 1)[1]))
                self.assertEqual(logged_comp, attacks[1])
                self.assertEqual(logged_bass, attacks[2])
                self.assertIn(f"Sent MIDI Note On: Melody=14 Comping={len(attacks[1])} Bass={len(attacks[2])}", log)
                programs = {m.channel: m.program for m in target.messages if m.type == "program_change"}
                self.assertEqual(programs, {0:0, 1:4, 2:32})
                self.assertIn("Estimated chord: C", log)
                self.assertIn("Active accompaniment chord: C", log)
                self.assertGreater(log.count("Active accompaniment chord:"), 1)
                # Every generated voice is released, even at finite run completion.
                for channel in (1, 2):
                    active = set()
                    for msg in target.messages:
                        if msg.channel != channel:
                            continue
                        if msg.type == "note_on":
                            self.assertNotIn(msg.note, active)
                            active.add(msg.note)
                        elif msg.type == "note_off":
                            active.remove(msg.note)
                    self.assertFalse(active)

    def test_normal_cli_keeps_instruments_and_still_sends_accompaniment(self):
        _, target, _, log = self.exercise(["--progression-aware", "--debug-harmony"])
        programs = {m.channel: m.program for m in target.messages if m.type == "program_change"}
        self.assertEqual(programs, {0:0, 1:0, 2:32})
        for channel in (1, 2):
            self.assertTrue(any(m.type == "note_on" and m.channel == channel for m in target.messages))
        self.assertNotIn("Comping notes:", log)
        self.assertIn("Add --debug-accomp", log)

    def test_audit_does_not_count_failed_send(self):
        class Broken:
            def send(self, message):
                raise OSError("failed")
        output = AuditedOutput(Broken())
        with self.assertRaises(OSError):
            output.send(mido.Message("note_on", note=60))
        self.assertEqual(output.summary(), "Sent MIDI Note On: Melody=0 Comping=0 Bass=0")


if __name__ == "__main__":
    unittest.main()
