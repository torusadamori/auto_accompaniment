"""Read-only input diagnostics. Raw mode bypasses Mido and all music logic."""
import math
import time
from .midi_io import backend, input_port, resolve_port, forward_pending


class ReceptionStatus:
    def __init__(self):
        self.started = self.last_event = self.last_status = time.perf_counter()
        self.events = self.notes = 0

    def received(self, description, is_note):
        self.events += 1
        self.notes += int(is_note)
        self.last_event = time.perf_counter()
        print(f"RAW MIDI: {description}", flush=True)

    def heartbeat(self):
        now = time.perf_counter()
        if now - self.last_status >= 5:
            if self.events == 0:
                print(f"No MIDI events received for {now-self.started:.1f}s (port opened).", flush=True)
            else:
                print(f"Received events={self.events}, note events={self.notes}; "
                      f"last event {now-self.last_event:.1f}s ago.", flush=True)
            self.last_status = now

    def summary(self):
        print(f"Input summary: events={self.events}, note events={self.notes}", flush=True)
        if not self.events:
            print("Port opened, but NO MIDI events were received. Open success alone does not prove keyboard input.", flush=True)
        elif not self.notes:
            print("MIDI traffic received, but NO Note On/Off events received.", flush=True)


def validate_duration(seconds):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Seconds must be finite and >= 0 (0 means until Ctrl+C).")


def monitor(value, seconds=0):
    validate_duration(seconds)
    api = backend()
    names = api.get_input_names()
    name = resolve_port(value, names)
    print(f"Opening MIDI input: {name} (index {names.index(name)}, requested {value!r})", flush=True)
    print("Backend: mido.backends.rtmidi; polling=source.poll(); sleep=1ms", flush=True)
    try:
        with input_port(name) as source:
            print(f"Port opened successfully: {source.name}; API={getattr(source, 'api', 'unknown')}", flush=True)
            print("Waiting for MIDI events... Play the keyboard. Ctrl+C stops.", flush=True)
            status = ReceptionStatus()
            try:
                while not seconds or time.perf_counter() - status.started < seconds:
                    forward_pending(source, on_message=lambda msg: status.received(
                        str(msg), msg.type in ("note_on", "note_off")))
                    status.heartbeat()
                    time.sleep(0.001)
            finally:
                status.summary()
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"Mido input failed: {type(error).__name__}: {error}. "
                           "Check the current port list and other MIDI applications.") from error


def raw_description(data):
    if len(data) == 3 and data[0] & 0xF0 in (0x80, 0x90):
        kind = "note_on" if data[0] & 0xF0 == 0x90 else "note_off"
        return f"{kind} channel={data[0] & 15} note={data[1]} velocity={data[2]} bytes={data}", True
    return f"bytes={data}", False


def raw_monitor(value, seconds=0):
    validate_duration(seconds)
    import rtmidi
    midi = rtmidi.MidiIn()
    try:
        names = midi.get_ports()
        print(f"Backend: python-rtmidi {rtmidi.__version__} DIRECT; "
              f"API={rtmidi.get_api_name(midi.get_current_api())}; polling=get_message()", flush=True)
        for index, name in enumerate(names):
            print(f"  RAW INPUT {index}: {name}", flush=True)
        # Resolve directly against RtMidi's own list, independent of Mido helpers.
        if value in names:
            index = names.index(value)
        elif value.isdecimal() and int(value) < len(names):
            index = int(value)
        else:
            raise ValueError(f"Raw input {value!r} not found in the list above.")
        print(f"Opening MIDI input: {names[index]} (index {index}, requested {value!r})", flush=True)
        midi.open_port(index)
        midi.ignore_types(sysex=False, timing=False, active_sense=False)
        if not midi.is_port_open():
            raise RuntimeError("RtMidi returned without an open port.")
        print("Port opened successfully. All MIDI message types enabled.", flush=True)
        print("Waiting for MIDI events... Play the keyboard. Ctrl+C stops.", flush=True)
        status = ReceptionStatus()
        try:
            while not seconds or time.perf_counter() - status.started < seconds:
                for _ in range(256):
                    packet = midi.get_message()
                    if packet is None:
                        break
                    data, delta = packet
                    description, is_note = raw_description(data)
                    status.received(f"{description} delta={delta:.6f}s", is_note)
                status.heartbeat()
                time.sleep(0.001)
        finally:
            status.summary()
    except Exception as error:
        raise RuntimeError(f"Direct RtMidi input failed: {type(error).__name__}: {error}") from error
    finally:
        midi.close_port()
        midi.delete()
