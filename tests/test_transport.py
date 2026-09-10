import asyncio
import contextlib
import socket
import signal
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

from experiments.m3_app.python.led_relay import LedRelay
from m3.receiver import LedConnection, consume, run


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.relay = LedRelay(self.calls.append, port=0)
        self.relay.step()
        self.client = socket.create_connection(self.relay.listener.getsockname(), timeout=1)
        self.relay.step()

    def tearDown(self):
        self.client.close()
        self.relay.close()

    def command(self, data):
        self.client.sendall(data)
        self.relay.step()

    def test_split_and_coalesced_frames(self):
        self.command(b'1')
        self.assertEqual(self.calls, [False])
        self.command(b'\n0\n')
        self.assertEqual(self.client.recv(6), b'OK\nOK\n')
        self.assertEqual(self.calls, [False, True, False])

    def test_heartbeat_does_not_duplicate_rpc(self):
        self.command(b'1\n1\n')
        self.assertEqual(self.calls, [False, True])
        self.assertEqual(self.client.recv(6), b'OK\nOK\n')

    def test_eof_clears_led(self):
        self.command(b'1\n')
        self.assertEqual(self.client.recv(3), b'OK\n')
        self.client.close()
        self.relay.step()
        self.assertEqual(self.calls, [False, True, False])
        self.assertIsNone(self.relay.client)

    def test_timeout_clears_led(self):
        self.command(b'1\n')
        self.relay.last_command = time.monotonic() - 4
        self.relay.step()
        self.assertEqual(self.calls[-1], False)
        self.assertIsNone(self.relay.client)

    def test_invalid_command_clears_led(self):
        self.command(b'1\n')
        with self.assertLogs('m3.relay', level='ERROR'):
            self.command(b'ON\n')
        self.assertEqual(self.calls[-1], False)
        self.assertIsNone(self.relay.client)

    def test_oversized_command_is_bounded(self):
        with self.assertLogs('m3.relay', level='ERROR'):
            self.command(b'x' * 64)
        self.assertIsNone(self.relay.client)
        self.assertFalse(self.relay.pending)

    def test_second_owner_rejected(self):
        self.command(b'1\n')
        with socket.create_connection(self.relay.listener.getsockname(), timeout=1) as second:
            self.relay.step()
            self.assertEqual(second.recv(1), b'')
        self.assertTrue(self.calls[-1])

    def test_rpc_failure_never_acknowledges_on(self):
        def fail_on(state):
            self.calls.append(state)
            if state:
                raise TimeoutError('fake Bridge timeout')
        self.relay.set_led = fail_on
        with self.assertLogs('m3.relay', level='ERROR'):
            self.command(b'1\n')
        self.assertEqual(self.client.recv(3), b'')
        self.assertEqual(self.calls, [False, True, False])

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets require Linux Python")
    def test_unix_shared_path_roundtrip_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'led.sock')
            relay = LedRelay(self.calls.append, unix_path=path)
            try:
                relay.step()
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(1)
                    client.connect(path)
                    relay.step()
                    client.sendall(b'1\n')
                    relay.step()
                    self.assertEqual(client.recv(3), b'OK\n')
                    self.assertTrue(self.calls[-1])
            finally:
                relay.close()
            self.assertFalse(Path(path).exists())


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_parser_to_socket_to_fake_bridge(self):
        calls, failures = [], []
        relay = LedRelay(calls.append, port=0)
        stop = threading.Event()

        def serve():
            try:
                while not stop.is_set():
                    relay.step()
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=serve)
        thread.start()
        writer = task = None
        try:
            reader, writer = await asyncio.open_connection(*relay.listener.getsockname())
            led = LedConnection(reader, writer)
            await led.set(False)
            queue = asyncio.Queue()
            for packet in ('80 80 90 3c 50 81 40 50',
                           '80 80 80 3c 00', '80 80 90 40 00'):
                queue.put_nowait((time.monotonic(), bytes.fromhex(packet)))
            task = asyncio.create_task(consume(queue, led))
            async with asyncio.timeout(3):
                while calls != [False, True, False]:
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.01)
            self.assertFalse(failures)
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if writer:
                writer.close()
                await writer.wait_closed()
            stop.set()
            await asyncio.to_thread(thread.join, 2)
            relay.close()

    async def test_malformed_and_stale_packet_fail_session(self):
        class FakeLed:
            async def set(self, state):
                raise AssertionError('Should not output a partial malformed packet')
        for received, data, error in (
            (time.monotonic(), '80 80 90 3c 50 81 80 3c', ValueError),
            (time.monotonic() - 2, '80 80 90 3c 50', RuntimeError),
        ):
            queue = asyncio.Queue()
            queue.put_nowait((received, bytes.fromhex(data)))
            with self.assertRaises(error):
                await consume(queue, FakeLed())

    async def test_idle_heartbeat_keeps_current_state(self):
        calls = []
        class Finished(Exception):
            pass
        class FakeLed:
            async def set(self, state):
                calls.append(state)
                if len(calls) == 2:
                    raise Finished()
        queue = asyncio.Queue()
        queue.put_nowait((time.monotonic(), bytes.fromhex('80 80 90 3c 50')))
        with self.assertRaises(Finished):
            await asyncio.wait_for(consume(queue, FakeLed()), 2)
        self.assertEqual(calls, [True, True])

    async def test_connection_rejects_non_ack(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'ERROR\n')
        class Writer:
            def write(self, _):
                pass
            async def drain(self):
                pass
        with self.assertRaises(ConnectionError):
            await LedConnection(reader, Writer()).set(True)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode):
        calls = []
        final_off = asyncio.Event()
        peer_callback = None
        signal_handlers = {}

        def install_signal(signum, callback, *args):
            signal_handlers[signum] = lambda: callback(*args)

        class FakeLed:
            async def set(self, state):
                calls.append(state)
                if state:
                    if mode == 'relay-error':
                        raise ConnectionError('relay ACK lost')
                    peer_callback(None)
                if not state and len(calls) > 1:
                    final_off.set()

        class FakeClient:
            def __init__(self, address, disconnected_callback):
                nonlocal peer_callback
                peer_callback = disconnected_callback
                characteristic = SimpleNamespace(properties=['notify'])
                service = SimpleNamespace(get_characteristic=lambda _: characteristic)
                self.services = SimpleNamespace(get_service=lambda _: None if mode == 'missing-service' else service)

            async def __aenter__(self):
                if mode == 'connect-error':
                    raise RuntimeError('connect failed')
                return self

            async def __aexit__(self, *args):
                peer_callback(None)  # Local cleanup also fires the callback.
                # An OFF must not depend on slow BLE teardown completing.
                try:
                    await final_off.wait()
                except asyncio.CancelledError:
                    await final_off.wait()

            async def read_gatt_char(self, _):
                raise AssertionError('Notify must not depend on a GATT read')

            async def start_notify(self, _, callback):
                if mode == 'early-disconnect':
                    peer_callback(None)
                    await asyncio.Event().wait()
                if mode == 'signal':
                    signal_handlers[signal.SIGTERM]()
                    return
                if mode == 'notify-error':
                    raise RuntimeError('start_notify failed')
                if mode == 'overflow':
                    for _ in range(257):
                        callback(None, bytes.fromhex('80 80 f8'))
                elif mode == 'malformed':
                    callback(None, bytes.fromhex('80 80 90 3c'))
                else:
                    callback(None, bytes.fromhex('80 80 90 3c 50'))

        writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
        args = SimpleNamespace(scan=False, socket='fake.sock', port=None,
                               address='fake-phone', raw=False)
        loop = asyncio.get_running_loop()
        with patch.dict('sys.modules', {'bleak': SimpleNamespace(BleakClient=FakeClient, BleakScanner=None)}), \
             patch.object(loop, 'add_signal_handler', side_effect=install_signal), \
             patch.object(loop, 'remove_signal_handler'), \
             self.assertLogs('m3', level='INFO') as logs, \
             patch('m3.receiver.asyncio.open_unix_connection', create=True,
                   new=AsyncMock(return_value=(None, writer))), \
             patch('m3.receiver.LedConnection', return_value=FakeLed()):
            if mode in ('disconnect', 'early-disconnect', 'signal'):
                await asyncio.wait_for(run(args), 2)
                self.assertEqual(calls, [False, True, False] if mode == 'disconnect' else [False, False])
            else:
                error = (ValueError if mode == 'malformed' else
                         ConnectionError if mode == 'relay-error' else RuntimeError)
                with self.assertRaises(error):
                    await asyncio.wait_for(run(args), 2)
                self.assertFalse(calls[-1])
            self.assertTrue(final_off.is_set())
            output = '\n'.join(logs.output)
            self.assertIn('Session ended:', output)
            if mode == 'disconnect':
                self.assertIn('BLE MIDI subscribed:', output)
                self.assertIn('Session ended: BLE disconnected during receiving', output)
            elif mode == 'early-disconnect':
                self.assertIn('Session ended: BLE disconnected during notify subscription', output)
            elif mode == 'signal':
                self.assertIn('Session ended: signal SIGTERM', output)
                self.assertNotIn('Stop requested: BLE disconnected', output)
            elif mode == 'relay-error':
                self.assertIn('Session ended: MIDI/relay worker failed: ConnectionError: relay ACK lost', output)
            elif mode == 'notify-error':
                self.assertIn('Session ended: BLE notify subscription failed: RuntimeError: start_notify failed', output)
                self.assertNotIn('Stop requested: BLE disconnected', output)
            elif mode == 'missing-service':
                self.assertIn('BLE service validation failed', output)
                self.assertNotIn('BLE starting notify:', output)
            elif mode == 'connect-error':
                self.assertIn('BLE connecting failed: RuntimeError: connect failed', output)
            elif mode == 'overflow':
                self.assertIn('Session ended: BLE queue failed:', output)
            elif mode == 'malformed':
                self.assertIn('Session ended: MIDI/relay worker failed:', output)

    async def test_disconnect_sends_off_before_ble_cleanup(self):
        await self.exercise('disconnect')

    async def test_notify_error_survives_disconnect_callback_and_slow_cleanup(self):
        await self.exercise('notify-error')

    async def test_service_error_is_logged_before_cleanup(self):
        await self.exercise('missing-service')

    async def test_disconnect_before_notify_completes_is_logged(self):
        await self.exercise('early-disconnect')

    async def test_signal_reason_survives_cleanup_callback(self):
        await self.exercise('signal')

    async def test_relay_error_reason_survives_cleanup_callback(self):
        await self.exercise('relay-error')

    async def test_connection_error_is_logged(self):
        await self.exercise('connect-error')

    async def test_overflow_stops_session_and_sends_off(self):
        await self.exercise('overflow')

    async def test_parser_error_stops_session_and_sends_off(self):
        await self.exercise('malformed')


if __name__ == '__main__':
    unittest.main()
