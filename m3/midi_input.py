"""Discover and open the Linux MIDI input produced by the BLE-MIDI service.

BlueZ/WirePlumber (or BlueALSA) terminates BLE-MIDI on the UNO Q.  This module
therefore consumes an ALSA/JACK MIDI port; it never scans for, or connects to,
the iPhone as a GATT client.
"""

from dataclasses import dataclass
import re
import subprocess


@dataclass(frozen=True)
class MidiPort:
    api: int
    api_name: str
    index: int
    name: str

    @property
    def label(self) -> str:
        return f"{self.api_name}:{self.name}"


def _rtmidi(module=None):
    if module is not None:
        return module
    try:
        import rtmidi
    except ImportError as error:
        raise RuntimeError(
            "python-rtmidi is unavailable; run ./scripts/unoq/setup.sh"
        ) from error
    return rtmidi


def api_name(module, api) -> str:
    try:
        return module.get_api_display_name(api)
    except (AttributeError, TypeError, ValueError):
        try:
            return module.get_api_name(api)
        except (AttributeError, TypeError, ValueError):
            return str(api)


def list_input_ports(module=None) -> list[MidiPort]:
    """Enumerate all compiled RtMidi APIs without opening an input."""
    module = _rtmidi(module)
    found = []
    errors = []
    for api in module.get_compiled_api():
        midi_in = None
        try:
            midi_in = module.MidiIn(rtapi=api, name="unoq-midi-discovery")
            found.extend(MidiPort(api, api_name(module, api), index, name)
                         for index, name in enumerate(midi_in.get_ports()))
        except Exception as error:
            errors.append(f"{api_name(module, api)}={type(error).__name__}: {error}")
        finally:
            if midi_in is not None:
                try:
                    midi_in.close_port()
                except Exception:
                    pass
    if not found and errors and len(errors) == len(module.get_compiled_api()):
        raise RuntimeError("No usable RtMidi API: " + "; ".join(errors))
    return found


def select_input_port(ports: list[MidiPort], pattern: str) -> MidiPort | None:
    try:
        matcher = re.compile(pattern)
    except re.error as error:
        raise RuntimeError(f"Invalid UNOQ_MIDI_PORT_PATTERN {pattern!r}: {error}") from error
    return next((port for port in ports if matcher.search(port.label)), None)


def connected_bluez_devices() -> set[str] | None:
    """Return current BlueZ peer identities, or None when status is unavailable."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices", "Connected"], text=True,
            capture_output=True, timeout=3, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


class OpenMidiInput:
    """An opened RtMidi input. Callback receives (timestamp, raw bytes)."""

    def __init__(self, port: MidiPort, callback, module=None):
        self.port = port
        self._module = _rtmidi(module)
        self._input = self._module.MidiIn(rtapi=port.api, name="unoq-m3-receiver")
        try:
            self._input.ignore_types(sysex=False, timing=False, active_sense=False)

            def receive(event, _user_data=None):
                message, delta = event
                callback(float(delta), bytes(message))

            self._input.set_callback(receive)
            self._input.open_port(port.index, "unoq-m3-receiver")
        except Exception:
            self.close()
            raise

    def close(self):
        if self._input is not None:
            try:
                self._input.cancel_callback()
            finally:
                self._input.close_port()
                self._input = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
