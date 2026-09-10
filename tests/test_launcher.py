"""Launcher filesystem/subprocess tests; do not access a real App, BLE or socket."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from m3 import launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'App with spaces'
        self.app.mkdir()
        (self.app / 'app.yaml').write_text('existing app configuration')
        self.source = self.root / 'source'
        for relative in launcher.FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'new M3 content')

    def put_old(self, relative, content=b'local edit'):
        path = self.app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_identical_files_need_no_sync_or_backup(self):
        for relative in launcher.FILES:
            self.put_old(relative, b'new M3 content')
        self.assertEqual(launcher.plan_sync(self.app, self.source), [])
        self.assertIsNone(launcher.sync_files(self.app, []))
        self.assertFalse((self.app / '.m3-backups').exists())

    def test_local_edit_refused_before_any_file_written(self):
        path = self.put_old('sketch/sketch.ino')
        with self.assertRaisesRegex(RuntimeError, 'Local App edit preserved'):
            launcher.plan_sync(self.app, self.source, history=lambda _: set())
        self.assertEqual(path.read_bytes(), b'local edit')
        self.assertFalse((self.app / 'python/main.py').exists())

    def test_explicit_sync_preserves_backups_and_config(self):
        old = self.put_old('python/main.py')
        plan = launcher.plan_sync(self.app, self.source, allow_existing=True)
        backup = launcher.sync_files(self.app, plan)
        self.assertEqual((backup / 'python/main.py').read_bytes(), b'local edit')
        self.assertEqual(old.read_bytes(), b'new M3 content')
        self.assertEqual((self.app / 'app.yaml').read_text(), 'existing app configuration')

    def test_known_previous_revision_updates_with_backup(self):
        self.put_old('python/main.py', b'previous M3')
        plan = launcher.plan_sync(self.app, self.source, history=lambda _: {b'previous M3'})
        backup = launcher.sync_files(self.app, plan)
        self.assertEqual((backup / 'python/main.py').read_bytes(), b'previous M3')

    def test_existing_app_required(self):
        (self.app / 'app.yaml').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Existing App Lab copy'):
            launcher.plan_sync(self.app, self.source)

    def test_directory_destination_refused(self):
        (self.app / 'python/main.py').mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, 'non-regular'):
            launcher.plan_sync(self.app, self.source)

    def test_backup_failure_leaves_originals(self):
        path = self.put_old('python/main.py')
        plan = launcher.plan_sync(self.app, self.source, allow_existing=True)
        with patch('m3.launcher.shutil.copy2', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                launcher.sync_files(self.app, plan)
        self.assertEqual(path.read_bytes(), b'local edit')
        self.assertFalse((self.app / 'sketch/sketch.ino').exists())

    def test_missing_socket_explains_gui_run(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, 'Relay not running'):
                launcher.wait_for_socket(self.app / 'missing.sock', 0)
        self.assertIn('Run', output.getvalue())

    def test_non_socket_file_is_preserved(self):
        path = self.app / 'm3-led.sock'
        path.write_text('do not delete')
        with self.assertRaisesRegex(RuntimeError, 'Not a Unix socket'):
            launcher.wait_for_socket(path, 0)
        self.assertEqual(path.read_text(), 'do not delete')

    def test_dry_run_does_not_pull_copy_wait_or_launch(self):
        with patch('m3.launcher.sync_files') as sync, \
             patch('m3.launcher.subprocess.run') as run, \
             patch('m3.launcher.os.execv') as execute, \
             patch('m3.launcher.wait_for_socket') as wait, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.main(['--app-dir', str(self.app), '--dry-run', '--pull'])
        for mock in (sync, run, execute, wait):
            mock.assert_not_called()
        self.assertFalse((self.app / 'python').exists())
        self.assertIn('would start', output.getvalue())

    def test_running_app_is_not_overwritten(self):
        (self.app / 'm3-led.sock').touch()
        with contextlib.redirect_stdout(io.StringIO()), \
             patch('m3.launcher.sync_files') as sync:
            with self.assertRaisesRegex(RuntimeError, 'stop M3 in App Lab'):
                launcher.main(['--app-dir', str(self.app)])
        sync.assert_not_called()

    def test_dirty_pull_refused(self):
        with patch('m3.launcher.git', return_value=b' M edited.py'), \
             patch('m3.launcher.subprocess.run') as run, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'local changes'):
                launcher.main(['--pull'])
        run.assert_not_called()

    def test_pull_reloads_launcher_without_repeating_pull(self):
        with patch('m3.launcher.git', return_value=b''), \
             patch('m3.launcher.subprocess.run') as run, \
             patch('m3.launcher.os.execv') as execute, \
             contextlib.redirect_stdout(io.StringIO()):
            launcher.main(['--pull', '--raw'])
        self.assertEqual(run.call_args.args[0], ['git', 'pull', '--ff-only'])
        self.assertEqual(execute.call_args.args[1][-3:], ['-m', 'm3.launcher', '--raw'])

    def test_venv_receiver_arguments_and_no_copy_when_current(self):
        for relative in launcher.FILES:
            self.put_old(relative, (launcher.REPO / 'experiments/m3_app' / relative).read_bytes())
        with patch('importlib.metadata.version', return_value='1.5.8'), \
             patch('m3.launcher.wait_for_socket') as wait, \
             patch('m3.launcher.os.chdir'), \
             patch('m3.launcher.os.execv') as execute, \
             contextlib.redirect_stdout(io.StringIO()):
            launcher.main(['--app-dir', str(self.app), '--raw'])
        args = execute.call_args.args[1]
        self.assertEqual(args[0], launcher.sys.executable)
        self.assertEqual(args[2:4], ['-m', 'm3.receiver'])
        self.assertNotIn('9C:C3:94:81:01:53', args)
        self.assertIn('(?i)(bluez|ble[ -]?midi|toru1)', args)
        self.assertIn(str(self.app / 'm3-led.sock'), args)
        self.assertEqual(args[-1], '--raw')
        wait.assert_called_once()


if __name__ == '__main__':
    unittest.main()
