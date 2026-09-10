"""Receive the UNO Q's local Linux MIDI input and drive the App Lab relay."""

import argparse
import asyncio
import contextlib
import logging
import signal
import time

from .midi import ActiveNotes, MidiEvent
from .midi_input import (
    OpenMidiInput, connected_bluez_devices, list_input_ports, select_input_port,
)

LOG = logging.getLogger("m3")
RESET = object()


class LedConnection:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def set(self, state):
        async with asyncio.timeout(2):
            self.writer.write(b"1\n" if state else b"0\n")
            await self.writer.drain()
            if await self.reader.readline() != b"OK\n":
                raise ConnectionError("Bridge relay rejected command or disconnected")


def native_event(message: bytes) -> MidiEvent | None:
    """Convert one RtMidi MIDI 1.0 message to the existing ActiveNotes event."""
    if not message or message[0] < 0x80:
        raise ValueError(f"Invalid native MIDI message: {message.hex(' ')}")
    status = message[0]
    if status == 0xF0:  # ActiveNotes intentionally ignores SysEx.
        return None
    if status < 0xF0:
        size = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
    elif status in (0xF1, 0xF3):
        size = 1
    elif status == 0xF2:
        size = 2
    elif status == 0xF6 or status >= 0xF8:
        size = 0
    else:
        return None
    data = message[1:1 + size]
    if len(data) != size or any(byte >= 0x80 for byte in data):
        raise ValueError(f"Truncated/invalid native MIDI message: {message.hex(' ')}")
    return MidiEvent(status, tuple(data))


async def consume(queue, led, raw=False):
    notes = ActiveNotes()
    state = False
    last_send = time.monotonic()
    while True:
        # Heartbeat continues under a stream containing only non-note messages.
        remaining = max(0.001, 1 - (time.monotonic() - last_send))
        try:
            received, packet = await asyncio.wait_for(queue.get(), remaining)
        except TimeoutError:
            await led.set(state)
            last_send = time.monotonic()
            continue
        if packet is RESET or time.monotonic() - received > 1:
            message = notes.apply(MidiEvent(0xFF))
            if state:
                await led.set(False)
                last_send = time.monotonic()
            state = False
            LOG.info("%s", message)
            continue
        if raw:
            LOG.info("MIDI %.6f %s", received, packet.hex(" "))
        event = native_event(packet)
        message = notes.apply(event) if event else None
        if message:
            LOG.info("rx=%.6f %s", received, message)
        new_state = bool(notes.notes)
        if new_state != state:
            await led.set(new_state)
            state = new_state
            last_send = time.monotonic()
        if time.monotonic() - last_send >= 1:
            await led.set(state)
            last_send = time.monotonic()


async def monitor_midi(queue, stopped, pattern, retry_seconds, module=None,
                       peer_reader=connected_bluez_devices):
    """Open the matching local input and automatically recover if it disappears."""
    loop = asyncio.get_running_loop()
    active = None
    waiting_logged = False
    previous_peers = None

    def enqueue(_delta, message):
        def put():
            if stopped.is_set():
                return
            if queue.full():
                while not queue.empty():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                queue.put_nowait((time.monotonic(), RESET))
                LOG.error("MIDI queue overflow; active notes cleared")
                return
            queue.put_nowait((time.monotonic(), message))
        loop.call_soon_threadsafe(put)

    try:
        while not stopped.is_set():
            ports = list_input_ports(module)
            selected = select_input_port(ports, pattern)
            if active is None:
                if selected is None:
                    if not waiting_logged:
                        detail = ", ".join(port.label for port in ports) or "no MIDI inputs"
                        LOG.warning("Waiting for Linux BLE-MIDI input matching %r (%s)", pattern, detail)
                        waiting_logged = True
                else:
                    active = OpenMidiInput(selected, enqueue, module)
                    waiting_logged = False
                    LOG.info("Linux MIDI input opened: %s", selected.label)
            elif selected is None or (selected.api, selected.name) != (active.port.api, active.port.name):
                LOG.warning("Linux MIDI input disconnected: %s; waiting for reconnection", active.port.label)
                active.close()
                active = None
                queue.put_nowait((time.monotonic(), RESET))
            peers = peer_reader()
            if previous_peers and peers is not None and not previous_peers.issubset(peers):
                LOG.warning("BlueZ peer disconnected; active notes cleared; waiting for reconnection")
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait((time.monotonic(), RESET))
            if peers is not None:
                previous_peers = peers
            try:
                await asyncio.wait_for(stopped.wait(), retry_seconds)
            except TimeoutError:
                pass
    finally:
        if active is not None:
            active.close()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait((time.monotonic(), RESET))


async def run(args):

    connection = (asyncio.open_unix_connection(args.socket, limit=64) if args.socket
                  else asyncio.open_connection("127.0.0.1", args.port, limit=64))
    reader, writer = await asyncio.wait_for(connection, 3)
    led = LedConnection(reader, writer)
    queue = asyncio.Queue(maxsize=256)
    stopped = asyncio.Event()
    tasks = []
    loop = asyncio.get_running_loop()
    stop_reason = None
    failure = None
    installed_signals = []

    def request_stop(reason):
        nonlocal stop_reason
        if stop_reason is None:
            stop_reason = reason
            LOG.info("Stop requested: %s", reason)
        stopped.set()

    def record_failure(source, error):
        nonlocal failure
        if failure is None:
            failure = (source, error)
            LOG.error("%s failed: %s: %s", source, type(error).__name__, error,
                      exc_info=True)
        request_stop(f"{source} failed: {type(error).__name__}: {error}")

    async def worker():
        try:
            await consume(queue, led, args.raw)
        except Exception as error:
            record_failure("MIDI/relay worker", error)
            raise

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, request_stop, f"signal {signum.name}")
            installed_signals.append(signum)
        LOG.info("Relay connected; requesting initial LED OFF")
        await led.set(False)
        # Heartbeats continue while the iPhone is disconnected and the local
        # BLE-MIDI port is waiting to reappear.
        tasks = [asyncio.create_task(worker(), name="midi-relay"),
                 asyncio.create_task(
                     monitor_midi(queue, stopped, args.port_pattern, args.retry_seconds),
                     name="midi-input"),
                 asyncio.create_task(stopped.wait(), name="stop-waiter")]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        LOG.info("Session wakeup: %s", ", ".join(sorted(t.get_name() for t in done)))
        if failure is not None:
            raise failure[1]
        for task in done:
            task.result()
        if stop_reason is None:
            raise RuntimeError("Session task completed without a stop reason")
    except asyncio.CancelledError:
        request_stop("receiver task cancelled")
        raise
    except Exception as error:
        record_failure("session", error)
        raise
    finally:
        request_stop(stop_reason or "session cleanup")
        for task in tasks:
            task.cancel()
        # Stop output before waiting for input backend cleanup.
        if tasks:
            await asyncio.gather(tasks[0], return_exceptions=True)
        try:
            await led.set(False)
        except Exception as error:
            record_failure("Final LED OFF (check App Lab / MCU)", error)
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), 2)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Retain failures that arrive during teardown as well.
        for task, result in zip(tasks, results):
            if isinstance(result, Exception):
                LOG.debug("Task %s ended with %r", task.get_name(), result)
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        reason = (f"{failure[0]} failed: {type(failure[1]).__name__}: {failure[1]}"
                  if failure else stop_reason)
        LOG.info("Session ended: %s; LED OFF requested", reason)
    if failure is not None:
        # A failure first observed during cleanup must also produce a nonzero exit.
        raise failure[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--socket", help="Host path to the shared App Lab m3-led.sock")
    transport.add_argument("--port", type=int, help="Optional loopback TCP relay (tests only)")
    parser.add_argument("--port-pattern", default="(?i)(bluez|ble[ -]?midi|toru1)",
                        help="Regex selecting the Linux ALSA/JACK MIDI input")
    parser.add_argument("--retry-seconds", type=float, default=2.0)
    parser.add_argument("--raw", action="store_true", help="Log native MIDI bytes")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser.parse_args()
    if not (args.socket or args.port):
        parser.error("--socket is required (or --port for a loopback test)")
    if args.retry_seconds <= 0:
        parser.error("--retry-seconds must be positive")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception:
        LOG.exception("M3 stopped")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
