import asyncio
import unittest

from m3.receiver import RESET, monitor_midi
from m3.midi_input import (
    MidiPort, OpenMidiInput, connected_bluez_devices, list_input_ports, select_input_port,
)


class FakeMidiIn:
    ports = ["Midi Through", "BLE MIDI Server:toru1"]

    def __init__(self, rtapi, name):
        self.api = rtapi
        self.callback = None
        self.opened = None

    def get_ports(self):
        return list(self.ports)

    def ignore_types(self, **_):
        pass

    def set_callback(self, callback):
        self.callback = callback

    def open_port(self, index, _name):
        self.opened = index

    def cancel_callback(self):
        self.callback = None

    def close_port(self):
        self.opened = None


class FakeRtMidi:
    MidiIn = FakeMidiIn

    @staticmethod
    def get_compiled_api():
        return [2]

    @staticmethod
    def get_api_display_name(_api):
        return "ALSA"


class MidiInputTests(unittest.TestCase):
    def test_discovers_and_selects_ble_server_without_address(self):
        ports = list_input_ports(FakeRtMidi)
        self.assertEqual([port.label for port in ports],
                         ["ALSA:Midi Through", "ALSA:BLE MIDI Server:toru1"])
        selected = select_input_port(ports, "(?i)(ble.*midi|toru1)")
        self.assertEqual(selected.name, "BLE MIDI Server:toru1")

    def test_bad_pattern_has_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid UNOQ_MIDI_PORT_PATTERN"):
            select_input_port([], "[")

    def test_open_input_forwards_native_bytes(self):
        received = []
        port = MidiPort(2, "ALSA", 1, "BLE MIDI Server:toru1")
        opened = OpenMidiInput(port, lambda delta, data: received.append((delta, data)), FakeRtMidi)
        opened._input.callback(([0x90, 60, 100], 0.25), None)
        opened.close()
        self.assertEqual(received, [(0.25, bytes([0x90, 60, 100]))])

    def test_connected_devices_is_read_only_and_parsed(self):
        result = type("Result", (), {
            "returncode": 0,
            "stdout": "Device AA:BB iPhone\n",
        })()
        from unittest.mock import patch
        with patch("m3.midi_input.subprocess.run", return_value=result) as run:
            self.assertEqual(connected_bluez_devices(), {"Device AA:BB iPhone"})
        self.assertEqual(run.call_args.args[0], ["bluetoothctl", "devices", "Connected"])


class MidiMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_bluez_peer_disconnect_enqueues_safe_reset(self):
        peers = iter(({"Device AA iPhone"}, set(), set()))
        stopped = asyncio.Event()
        queue = asyncio.Queue()

        async def finish():
            await asyncio.sleep(0.035)
            stopped.set()

        task = asyncio.create_task(monitor_midi(
            queue, stopped, "toru1", 0.01, FakeRtMidi,
            peer_reader=lambda: next(peers, set()),
        ))
        await finish()
        await task
        items = []
        while not queue.empty():
            items.append(queue.get_nowait()[1])
        self.assertIn(RESET, items)


if __name__ == "__main__":
    unittest.main()
