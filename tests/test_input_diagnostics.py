from contextlib import redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import mido
from autoaccomp.input_diagnostics import monitor, raw_monitor, raw_description
from autoaccomp.main import parser


class Clock:
    now = 0.0

    def counter(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.1)


class DiagnosticsTests(unittest.TestCase):
    def test_mido_poll_reports_events_and_selected_port(self):
        clock = Clock()
        api = MagicMock()
        api.get_input_names.return_value = ["OVO 0", "MIDIFlex4 1"]
        source = api.open_input.return_value.__enter__.return_value
        source.name, source.api = "MIDIFlex4 1", "WINDOWS_MM"
        source.poll.side_effect = [mido.Message("note_on", note=48, velocity=85),
                                   mido.Message("note_on", note=48, velocity=0)] + [None]*20
        log = io.StringIO()
        with patch("autoaccomp.input_diagnostics.backend", return_value=api), \
             patch("autoaccomp.midi_io.backend", return_value=api), \
             patch("time.perf_counter", clock.counter), patch("time.sleep", clock.sleep), redirect_stdout(log):
            monitor("1", 0.2)
        api.open_input.assert_called_once_with("MIDIFlex4 1")
        self.assertIn("Port opened successfully", log.getvalue())
        self.assertIn("RAW MIDI: note_on channel=0 note=48 velocity=85", log.getvalue())
        self.assertIn("velocity=0", log.getvalue())
        self.assertIn("Input summary: events=2, note events=2", log.getvalue())
        api.open_input.return_value.__exit__.assert_called_once()

    def raw_run(self, packets, seconds=0.2, value="MIDIFlex4 1", error=None):
        clock, midi, log = Clock(), MagicMock(), io.StringIO()
        midi.get_ports.return_value = ["OVO 0", "MIDIFlex4 1"]
        midi.get_current_api.return_value = 4
        midi.get_message.side_effect = list(packets) + [None]*300
        midi.is_port_open.return_value = True
        if error:
            midi.open_port.side_effect = error
        module = SimpleNamespace(MidiIn=lambda: midi, __version__="test", get_api_name=lambda _: "winmm")
        with patch.dict("sys.modules", rtmidi=module), patch("time.perf_counter", clock.counter), \
             patch("time.sleep", clock.sleep), redirect_stdout(log):
            try:
                raw_monitor(value, seconds)
            except RuntimeError as failure:
                if not error:
                    raise
                self.assertIn(str(error), str(failure))
        return midi, log.getvalue()

    def test_raw_direct_bytes_no_mido_and_name_index_agree(self):
        for value in ("1", "MIDIFlex4 1"):
            midi, log = self.raw_run([([0x90, 48, 85], 0.1), ([0x80, 48, 0], 0.2)], value=value)
            midi.open_port.assert_called_once_with(1)
            midi.ignore_types.assert_called_once_with(sysex=False, timing=False, active_sense=False)
            midi.close_port.assert_called_once()
            midi.delete.assert_called_once()
            self.assertIn("note_off channel=0 note=48 velocity=0", log)
            self.assertIn("Input summary: events=2, note events=2", log)

    def test_idle_is_explicit_and_rate_limited(self):
        _, log = self.raw_run([], seconds=11)
        self.assertEqual(log.count("No MIDI events received for"), 2)
        self.assertIn("Port opened, but NO MIDI events were received", log)

    def test_clock_traffic_is_not_claimed_as_notes(self):
        _, log = self.raw_run([([0xF8], 0.01)])
        self.assertIn("events=1, note events=0", log)
        self.assertIn("NO Note On/Off", log)

    def test_open_failure_is_not_hidden_and_releases_resources(self):
        midi, log = self.raw_run([], error=OSError("device occupied"))
        self.assertNotIn("Port opened successfully", log)
        midi.close_port.assert_called_once()
        midi.delete.assert_called_once()

    def test_raw_cli_and_velocity_zero_remain_raw(self):
        args = parser().parse_args(["raw-monitor", "--input", "1", "--seconds", "10"])
        self.assertEqual(args.input, "1")
        description, is_note = raw_description([0x92, 60, 0])
        self.assertTrue(is_note)
        self.assertIn("note_on channel=2 note=60 velocity=0", description)


if __name__ == "__main__":
    unittest.main()
