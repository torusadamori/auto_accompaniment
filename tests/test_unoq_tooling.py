"""Tests for reusable UNO Q deployment and diagnostic primitives."""

import contextlib
import asyncio
import io
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from unoq import deploy, doctor, runtime


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "App with spaces"
        self.app.mkdir()
        (self.app / "app.yaml").write_text("owned by App Lab")
        self.source = self.root / "source"
        (self.source / "python").mkdir(parents=True)
        (self.source / "python/main.py").write_bytes(b"repo version")

    def test_identical_deploy_is_idempotent(self):
        destination = self.app / "python/main.py"
        destination.parent.mkdir()
        destination.write_bytes(b"repo version")
        self.assertEqual(deploy.plan_sync(self.app, self.source, ["python/main.py"]), [])

    def test_hand_edit_is_preserved(self):
        destination = self.app / "python/main.py"
        destination.parent.mkdir()
        destination.write_bytes(b"hand edit")
        with self.assertRaisesRegex(RuntimeError, "hand edit preserved"):
            deploy.plan_sync(self.app, self.source, ["python/main.py"],
                             history=lambda *_: set())
        self.assertEqual(destination.read_bytes(), b"hand edit")

    def test_explicit_replace_backs_up_before_atomic_sync(self):
        destination = self.app / "python/main.py"
        destination.parent.mkdir()
        destination.write_bytes(b"hand edit")
        plan = deploy.plan_sync(self.app, self.source, ["python/main.py"], True)
        backup = deploy.sync_files(self.app, plan)
        self.assertEqual((backup / "python/main.py").read_bytes(), b"hand edit")
        self.assertEqual(destination.read_bytes(), b"repo version")
        self.assertEqual((self.app / "app.yaml").read_text(), "owned by App Lab")

    def test_dry_run_never_writes(self):
        with patch("unoq.deploy.REPO", self.root), \
                patch("unoq.deploy.plan_sync", return_value=[
                (self.app / "python/main.py", b"repo version", None)]), \
                patch("unoq.deploy.sync_files") as sync, \
                contextlib.redirect_stdout(io.StringIO()):
            result = deploy.main([
                "--app-dir", str(self.app), "--source-dir", str(self.source),
                "--socket", str(self.app / "relay.sock"),
                "--file", "python/main.py", "--dry-run",
            ])
        self.assertEqual(result, 0)
        sync.assert_not_called()

    def test_unsafe_relative_path_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            deploy.plan_sync(self.app, self.source, ["../outside"])

    def test_symlink_app_manifest_is_rejected(self):
        manifest = self.app / "app.yaml"
        target = self.root / "real-app.yaml"
        target.write_text("real")
        manifest.unlink()
        try:
            manifest.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation is not permitted")
        with self.assertRaisesRegex(RuntimeError, "Existing App Lab"):
            deploy.plan_sync(self.app, self.source, ["python/main.py"])

    def test_stop_running_uses_official_cli_and_waits_for_socket(self):
        relay_socket = self.app / "relay.sock"
        with patch("unoq.deploy.socket_present", side_effect=[True, False]), \
                patch("unoq.deploy.relay_probe", return_value=(True, "reply")), \
                patch("unoq.deploy.subprocess.run") as run:
            deploy.stop_running_app(self.app, relay_socket)
        run.assert_called_once_with(
            ["arduino-app-cli", "app", "stop", str(self.app)], check=True
        )

    def test_stop_removes_only_unreachable_socket_after_confirmed_stop(self):
        relay_socket = self.app / "relay.sock"
        with patch("unoq.deploy.socket_present", return_value=True), \
                patch("unoq.deploy.relay_probe", return_value=(False, "refused")), \
                patch("unoq.deploy.app_lab_status", return_value=("stopped", "Stopped")), \
                patch("unoq.deploy.subprocess.run"), \
                patch("unoq.deploy.remove_stale_socket") as remove:
            deploy.stop_running_app(self.app, relay_socket, 1)
        remove.assert_called_once_with(relay_socket, self.app, app_stopped=True)

    def test_live_socket_is_preserved_after_stop_timeout(self):
        relay_socket = self.app / "relay.sock"
        with patch("unoq.deploy.socket_present", return_value=True), \
                patch("unoq.deploy.relay_probe", return_value=(True, "reply")), \
                patch("unoq.deploy.time.monotonic", side_effect=[0, 2]), \
                patch("unoq.deploy.subprocess.run"), \
                patch("unoq.deploy.remove_stale_socket") as remove:
            with self.assertRaisesRegex(RuntimeError, "still answers"):
                deploy.stop_running_app(self.app, relay_socket, 1)
        remove.assert_not_called()


class RuntimeTests(unittest.TestCase):
    def test_app_status_parses_official_json(self):
        result = type("Result", (), {
            "returncode": 0,
            "stdout": '{"apps":[{"name":"m3-ble-to-led","status":"Running"}]}',
            "stderr": "",
        })()
        with patch("unoq.runtime.subprocess.run", return_value=result):
            self.assertEqual(runtime.app_lab_status(Path("/apps/m3-ble-to-led")),
                             ("running", "Running"))

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets require AF_UNIX")
    def test_stale_cleanup_requires_confirmed_stopped_app(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.sock"
            listener = socket.socket(socket.AF_UNIX)
            try:
                listener.bind(str(path))
            finally:
                listener.close()
            with patch("unoq.runtime.relay_probe", return_value=(False, "refused")), \
                    patch("unoq.runtime.app_lab_status", return_value=("running", "Running")):
                with self.assertRaisesRegex(RuntimeError, "socket preserved"):
                    runtime.remove_stale_socket(path, path.parent)
            self.assertTrue(path.exists())
            with patch("unoq.runtime.relay_probe", return_value=(False, "refused")), \
                    patch("unoq.runtime.app_lab_status", return_value=("stopped", "Stopped")), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(runtime.remove_stale_socket(path, path.parent))
            self.assertFalse(path.exists())


class DoctorReportTests(unittest.TestCase):
    def test_warn_does_not_fail_but_fail_does(self):
        report = doctor.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            report.add("PASS", "one", "ok")
            report.add("WARN", "two", "optional")
        self.assertEqual(report.exit_code(), 0)
        with contextlib.redirect_stdout(io.StringIO()):
            report.add("FAIL", "three", "broken")
        self.assertEqual(report.exit_code(), 1)


class DoctorBleakTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_known_bluez_peer_and_probes_notify_without_scan(self):
        calls = []
        characteristic = SimpleNamespace(properties=["notify"])
        service = SimpleNamespace(get_characteristic=lambda uuid: (
            characteristic if uuid == "characteristic" else None))

        class FakeClient:
            def __init__(self, peer, timeout):
                calls.append(("client", peer, timeout))
                self.services = SimpleNamespace(get_service=lambda uuid: (
                    service if uuid == "service" else None))
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return None
            async def start_notify(self, target, callback):
                calls.append(("start", target))
                callback(target, bytes.fromhex("80 80 90 3c 50"))
            async def stop_notify(self, target):
                calls.append(("stop", target))

        report = doctor.Report()
        with patch.dict("sys.modules", {"bleak": SimpleNamespace(BleakClient=FakeClient)}), \
                contextlib.redirect_stdout(io.StringIO()):
            await doctor.inspect_ble("AA:BB", "service", "characteristic", 3, 0, report)
        self.assertEqual(calls[0], ("client", "AA:BB", 3))
        self.assertEqual([item[0] for item in calls], ["client", "start", "stop"])
        self.assertTrue(any(name == "BLE notify path" and level == "PASS"
                            for level, name, _ in report.results))


if __name__ == "__main__":
    unittest.main()
