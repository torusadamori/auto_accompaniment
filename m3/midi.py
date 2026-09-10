"""BLE-MIDI 1.0 framing; only SysEx state survives notification boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MidiEvent:
    status: int
    data: tuple[int, ...] = ()


class BleMidiParser:
    def __init__(self):
        self.in_sysex = False

    def parse(self, packet: bytes) -> list[MidiEvent]:
        """Return complete events, skipping SysEx payload without buffering it.

        Reject malformed packets atomically. A caller must clear active notes
        and stop the session on ValueError, rather than losing a possible off.
        Timestamps are framing only: M3 deliberately renders on receipt.
        """
        try:
            return self._parse(packet)
        except ValueError:
            self.in_sysex = False
            raise

    def _parse(self, packet):
        if len(packet) < 2 or packet[0] & 0xC0 != 0x80:
            raise ValueError("Invalid BLE MIDI header/empty packet")
        events = []
        running = None
        i = 1
        require_timestamp = True
        while i < len(packet):
            if self.in_sysex and packet[i] < 0x80:
                i += 1
                continue
            timestamp = packet[i] >= 0x80
            if timestamp:
                i += 1
                if i == len(packet):
                    raise ValueError("Timestamp without MIDI message")
            elif require_timestamp:
                raise ValueError("Missing timestamp")

            status = packet[i]
            if status >= 0x80:
                if not timestamp:
                    raise ValueError("Status without timestamp")
                i += 1
                if self.in_sysex and status != 0xF7 and status < 0xF8:
                    raise ValueError("Non-realtime message inside SysEx")
                if status == 0xF0:
                    self.in_sysex = True
                    require_timestamp = True
                    continue
                if status == 0xF7:
                    if not self.in_sysex:
                        raise ValueError("EOX without SysEx")
                    self.in_sysex = False
                    require_timestamp = True
                    continue
                if status < 0xF0:
                    running = status
            else:
                if self.in_sysex or running is None:
                    raise ValueError("Data without running status")
                status = running

            if status < 0xF0:
                size = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
            elif status in (0xF1, 0xF3):
                size = 1
            elif status == 0xF2:
                size = 2
            elif status == 0xF6 or status >= 0xF8:
                size = 0
            else:
                raise ValueError("Undefined system common status")
            data = packet[i:i + size]
            if len(data) != size or any(b >= 0x80 for b in data):
                raise ValueError("Truncated/invalid MIDI data")
            events.append(MidiEvent(status, tuple(data)))
            i += size
            # BLE-MIDI keeps running status across system messages, but the
            # next running message then requires its own timestamp (RP-052).
            require_timestamp = status >= 0xF0
        return events


class ActiveNotes:
    """Key-down state keyed by (zero-based channel, note), across all channels."""

    def __init__(self):
        self.notes: set[tuple[int, int]] = set()

    def apply(self, event: MidiEvent) -> str | None:
        status, data = event.status, event.data
        kind, channel = status & 0xF0, status & 0x0F
        if kind in (0x80, 0x90):
            note, velocity = data
            on = kind == 0x90 and velocity > 0
            if on:
                self.notes.add((channel, note))
            else:
                self.notes.discard((channel, note))
            action = 'ON' if on else 'OFF'
            return (f"NOTE {action} ch={channel + 1} note={note} "
                    f"vel={velocity} active={len(self.notes)}")
        if kind == 0xB0 and data[0] in (120, 123, 124, 125, 126, 127):
            self.notes.difference_update({n for n in self.notes if n[0] == channel})
            return f"ALL OFF ch={channel + 1} cc={data[0]} active={len(self.notes)}"
        if status == 0xFF:
            self.notes.clear()
            return "SYSTEM RESET active=0"
        return None
