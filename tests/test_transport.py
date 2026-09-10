import asyncio
import contextlib
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from experiments.m3_app.python.led_relay import LedRelay
from m3.receiver import RESET, LedConnection, consume, native_event


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
            second.sendall(b'0\n')
            self.relay.step()
            self.assertEqual(second.recv(1), b'')
        self.assertTrue(self.calls[-1])

    def test_probe_can_run_beside_output_owner(self):
        self.command(b'1\n')
        self.assertEqual(self.client.recv(3), b'OK\n')
        with socket.create_connection(self.relay.listener.getsockname(), timeout=1) as observer:
            self.relay.step()
            observer.sendall(b'PROBE\n')
            self.relay.step()
            self.assertEqual(observer.recv(3), b'OK\n')
        self.assertTrue(self.calls[-1])
        self.assertIsNotNone(self.relay.client)

    def test_ping_checks_relay_without_taking_output_ownership(self):
        self.command(b'PING\n')
        self.assertEqual(self.client.recv(16), b'OK\n')
        self.client.close()
        self.relay.step()
        self.assertEqual(self.calls, [False])

    def test_probe_calls_bridge_without_changing_state_or_ownership(self):
        self.command(b'PROBE\n')
        self.assertEqual(self.client.recv(16), b'OK\n')
        self.assertEqual(self.calls, [False, False])
        self.client.close()
        self.relay.step()
        self.assertEqual(self.calls, [False, False])

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
            for packet in ('90 3c 50', '90 40 50', '80 3c 00', '90 40 00'):
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

    async def test_malformed_native_message_fails_session(self):
        class FakeLed:
            async def set(self, state):
                raise AssertionError('Should not output a partial malformed packet')
        queue = asyncio.Queue()
        queue.put_nowait((time.monotonic(), bytes.fromhex('90 3c')))
        with self.assertRaises(ValueError):
            await consume(queue, FakeLed())

    async def test_disconnect_marker_clears_active_notes(self):
        calls = []
        class Finished(Exception):
            pass
        class FakeLed:
            async def set(self, state):
                calls.append(state)
                if calls == [True, False]:
                    raise Finished()
        queue = asyncio.Queue()
        queue.put_nowait((time.monotonic(), bytes.fromhex('90 3c 50')))
        queue.put_nowait((time.monotonic(), RESET))
        with self.assertRaises(Finished):
            await consume(queue, FakeLed())
        self.assertEqual(calls, [True, False])

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
        queue.put_nowait((time.monotonic(), bytes.fromhex('90 3c 50')))
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

    def test_native_event_conversion(self):
        event = native_event(bytes.fromhex('91 40 7f'))
        self.assertEqual((event.status, event.data), (0x91, (0x40, 0x7f)))


if __name__ == '__main__':
    unittest.main()
