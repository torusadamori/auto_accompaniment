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
    overflow = False
    tasks = []
    loop = asyncio.get_running_loop()

    def notification(_characteristic, data):
        nonlocal overflow
        if stopped.is_set():
            return
        try:
            queue.put_nowait((time.monotonic(), bytes(data)))
        except asyncio.QueueFull:
            overflow = True
            stopped.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        await led.set(False)
        # Also sends OFF heartbeats while connecting/subscribing to BLE.
        worker = asyncio.create_task(consume(queue, led, args.raw))
        tasks.append(worker)

        async def receive():
            async with BleakClient(args.address, disconnected_callback=lambda _: stopped.set()) as client:
                service = client.services.get_service(SERVICE)
                characteristic = service.get_characteristic(CHARACTERISTIC) if service else None
                if characteristic is None or "notify" not in characteristic.properties:
                    raise RuntimeError("Peer does not expose BLE MIDI notifications")
                await client.read_gatt_char(characteristic)
                await client.start_notify(characteristic, notification)
                LOG.info("BLE MIDI subscribed: %s", args.address)
                await stopped.wait()

        tasks.extend([asyncio.create_task(receive()), asyncio.create_task(stopped.wait())])
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if overflow:
            raise RuntimeError("BLE queue overflow; session stopped")
    finally:
        stopped.set()
        for task in tasks:
            task.cancel()
        # Stop output first; BLE disconnect cleanup may itself be slow.
        if tasks:
            await asyncio.gather(tasks[0], return_exceptions=True)
        try:
            await led.set(False)
        except Exception:
            LOG.exception("Final LED OFF not acknowledged; check App Lab / MCU")
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), 2)
        await asyncio.gather(*tasks, return_exceptions=True)
        LOG.info("Session ended; restart receiver to reconnect with empty active notes")


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
