"""Tests for reusable UNO Q deployment and diagnostic primitives."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unoq import deploy, doctor


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
                patch("unoq.deploy.subprocess.run") as run:
            deploy.stop_running_app(self.app, relay_socket)
        run.assert_called_once_with(
            ["arduino-app-cli", "app", "stop", str(self.app)], check=True
        )


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


if __name__ == "__main__":
    unittest.main()
