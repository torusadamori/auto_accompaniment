"""Run on UNO Q host Linux: python -m m3.receiver --address ADDR --socket PATH."""

import argparse
import asyncio
import contextlib
import logging
import signal
import time

from .midi import ActiveNotes, BleMidiParser

SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
CHARACTERISTIC = "7772e5db-3868-4112-a1a9-f2669d106bf3"
LOG = logging.getLogger("m3")


class LedConnection:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def set(self, state):
        async with asyncio.timeout(2):
            self.writer.write(b"1\n" if state else b"0\n")
            await self.writer.drain()
            if await self.reader.readline() != b"OK\n":
                raise ConnectionError("Bridge relay rejected command or disconnected")


async def consume(queue, led, raw=False):
    parser, notes = BleMidiParser(), ActiveNotes()
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
        if time.monotonic() - received > 1:
            raise RuntimeError("BLE queue older than 1 second; stop instead of replaying stale notes")
        if raw:
            LOG.info("RX %.6f %s", received, packet.hex(" "))
        for event in parser.parse(packet):
            message = notes.apply(event)
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


async def run(args):
    from bleak import BleakClient, BleakScanner

    if args.scan:
        devices = await BleakScanner.discover(timeout=10, return_adv=True)
        for device, adv in devices.values():
            LOG.info("%s name=%r MIDI=%s", device.address, device.name,
                     SERVICE in [u.lower() for u in adv.service_uuids])
        return

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
    phase = "connecting"
    tearing_down = False
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

    def disconnected(_client):
        if tearing_down:
            LOG.debug("BLE disconnected during local cleanup")
        else:
            request_stop(f"BLE disconnected during {phase}")

    def notification(_characteristic, data):
        if stopped.is_set():
            return
        try:
            queue.put_nowait((time.monotonic(), bytes(data)))
        except asyncio.QueueFull:
            record_failure("BLE queue", RuntimeError("BLE queue overflow; session stopped"))

    async def worker():
        try:
            await consume(queue, led, args.raw)
        except Exception as error:
            record_failure("MIDI/relay worker", error)
            raise

    async def receive():
        nonlocal phase, tearing_down
        try:
            LOG.info("BLE connecting: %s", args.address)
            async with BleakClient(args.address, disconnected_callback=disconnected) as client:
                try:
                    phase = "service validation"
                    LOG.info("BLE connected; validating MIDI service")
                    service = client.services.get_service(SERVICE)
                    characteristic = service.get_characteristic(CHARACTERISTIC) if service else None
                    if characteristic is None or "notify" not in characteristic.properties:
                        raise RuntimeError("Peer does not expose BLE MIDI notifications")
                    phase = "notify subscription"
                    LOG.info("BLE starting notify: %s", CHARACTERISTIC)
                    # Prefer the successfully tested notify reception path. Do not
                    # gate it on a separate GATT read of the MIDI stream.
                    await client.start_notify(characteristic, notification)
                    phase = "receiving"
                    LOG.info("BLE MIDI subscribed: %s", args.address)
                    await stopped.wait()
                except Exception as error:
                    # Capture BEFORE __aexit__: disconnect callbacks/slow cleanup
                    # must not hide the original service/start_notify exception.
                    record_failure(f"BLE {phase}", error)
                    raise
                finally:
                    tearing_down = True
        except Exception as error:
            record_failure(f"BLE {phase}", error)
            raise

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, request_stop, f"signal {signum.name}")
            installed_signals.append(signum)
        LOG.info("Relay connected; requesting initial LED OFF")
        await led.set(False)
        # Heartbeats continue while connecting/subscribing to BLE.
        tasks = [asyncio.create_task(worker(), name="midi-relay"),
                 asyncio.create_task(receive(), name="ble-receiver"),
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
        tearing_down = True
        request_stop(stop_reason or "session cleanup")
        for task in tasks:
            task.cancel()
        # Stop output first; BLE disconnect cleanup may itself be slow.
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
        LOG.info("Session ended: %s; restart receiver to reconnect with empty active notes", reason)
    if failure is not None:
        # A failure first observed during cleanup must also produce a nonzero exit.
        raise failure[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--address", help="iPhone BlueZ address from the successful BLE test")
    target.add_argument("--scan", action="store_true")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--socket", help="Host path to the shared App Lab m3-led.sock")
    transport.add_argument("--port", type=int, help="Optional loopback TCP relay (tests only)")
    parser.add_argument("--raw", action="store_true")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser.parse_args()
    if args.address and not (args.socket or args.port):
        parser.error("--address requires --socket (or --port for a loopback test)")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception:
        LOG.exception("M3 stopped")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
