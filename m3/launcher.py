"""Safe preparation for the existing UNO Q M3 app; invoked by the shell launcher."""

import argparse
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
FILES = ('python/main.py', 'python/led_relay.py', 'sketch/sketch.ino')
DEFAULT_APP = '/home/arduino/ArduinoApps/m3-ble-to-led'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO)


def previous_versions(relative):
    path = 'experiments/m3_app/' + relative
    commits = git('log', '--format=%H', '--', path).decode().splitlines()
    return {git('show', f'{commit}:{path}') for commit in commits}


def plan_sync(app, source, allow_existing=False, history=previous_versions):
    """Validate ALL destinations before touching any file."""
    if app.is_symlink() or not app.is_dir() or not (app / 'app.yaml').is_file():
        raise RuntimeError(f'Existing App Lab copy with app.yaml required: {app}')
    plan = []
    for relative in FILES:
        destination = app / relative
        if (destination.parent.is_symlink() or destination.is_symlink()
                or (destination.exists() and not destination.is_file())):
            raise RuntimeError(f'Refusing non-regular/symlink destination: {destination}')
        content = (source / relative).read_bytes()
        old = destination.read_bytes() if destination.exists() else None
        if old == content:
            continue
        if old is not None and not allow_existing and old not in history(relative):
            raise RuntimeError(
                f'Local App edit preserved: {destination}\n'
                'Review with --dry-run --sync-existing, then use --sync-existing '
                'to replace it with a backup (App must be stopped).')
        plan.append((destination, content, old))
    return plan


def sync_files(app, plan):
    backup = None
    if any(old is not None for _, _, old in plan):
        backup = app / '.m3-backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        if (app / '.m3-backups').is_symlink():
            raise RuntimeError('Refusing symlink backup directory')
        backup.mkdir(parents=True)
        # Complete backups before replacing anything.
        for destination, _, old in plan:
            if old is not None:
                saved = backup / destination.relative_to(app)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
        print(f'M3: originals backed up to {backup}', flush=True)
    for destination, content, _ in plan:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
            temporary.chmod(0o644)
            os.replace(temporary, destination)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        print(f'M3: synced {destination}', flush=True)
    return backup


def socket_exists(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f'Not a Unix socket (not removed): {path}')
    return True


def wait_for_socket(path, seconds):
    deadline = time.monotonic() + seconds
    if not socket_exists(path):
        print('M3: App LabでM3 BLE to LEDをRunしてください。'
              f' Waiting up to {seconds:g}s for {path}', flush=True)
    while not socket_exists(path):
        if time.monotonic() >= deadline:
            raise RuntimeError('Relay not running. App LabでM3 BLE to LEDをRunしてください。'
                               ' Then rerun this command. No socket was deleted.')
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    if not os.access(path, os.R_OK | os.W_OK):
        raise RuntimeError(f'Relay socket permission denied: {path}. Use the App owner account.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, default=Path(os.environ.get('M3_APP_DIR', DEFAULT_APP)))
    parser.add_argument('--socket', type=Path, help='Default: APP_DIR/m3-led.sock')
    parser.add_argument('--port-pattern', default=os.environ.get(
        'UNOQ_MIDI_PORT_PATTERN', '(?i)(bluez|ble[ -]?midi|toru1)'))
    parser.add_argument('--wait-seconds', type=float, default=120)
    parser.add_argument('--raw', action='store_true', help='Log raw BLE packets')
    parser.add_argument('--dry-run', action='store_true', help='Inspect only; no pull, copy, socket connect or BLE')
    parser.add_argument('--pull', action='store_true', help='Clean tree only: git pull --ff-only, then reload launcher')
    parser.add_argument('--sync-existing', action='store_true', help='Allow replacing edited App files after backup')
    args = parser.parse_args(argv)
    if args.wait_seconds < 0 or not math.isfinite(args.wait_seconds):
        parser.error('--wait-seconds must be finite and nonnegative')
    app = args.app_dir.expanduser().absolute()
    path = args.socket.expanduser().absolute() if args.socket else app / 'm3-led.sock'
    print(f'M3: repo={REPO}\nM3: python={sys.executable}\nM3: app={app}\nM3: socket={path}', flush=True)
    if args.pull:
        if args.dry_run:
            print('M3: dry-run: would pull --ff-only; inspecting current checkout only', flush=True)
        else:
            if git('status', '--porcelain').strip():
                raise RuntimeError('Repo has local changes. Commit/preserve them before --pull; no stash/reset performed.')
            subprocess.run(['git', 'pull', '--ff-only'], cwd=REPO, check=True)
            remaining = [arg for arg in (sys.argv[1:] if argv is None else argv) if arg != '--pull']
            os.execv(sys.executable, [sys.executable, '-B', '-m', 'm3.launcher', *remaining])
            return
    plan = plan_sync(app, REPO / 'experiments/m3_app', args.sync_existing)
    for destination, _, old in plan:
        print(f'M3: {"backup + update" if old is not None else "create"}: {destination}', flush=True)
    if not plan:
        print('M3: App files already match; no copy or restart needed', flush=True)
    receiver_args = [sys.executable, '-B', '-m', 'm3.receiver', '--socket', str(path),
                     '--port-pattern', args.port_pattern]
    if args.raw:
        receiver_args.append('--raw')
    if args.dry_run:
        print(f'M3: socket present={socket_exists(path)} (listener not probed)', flush=True)
        print(f'M3: would start {receiver_args!r}', flush=True)
        return
    # Do not modify a running App. Even a stale socket is preserved for inspection.
    if plan and (path.exists() or (app / 'm3-led.sock').exists()):
        raise RuntimeError('App sync needed: stop M3 in App Lab first, then rerun. '
                           'Existing socket/files were not changed.')
    from importlib.metadata import version, PackageNotFoundError
    try:
        installed = version('python-rtmidi')
    except PackageNotFoundError as error:
        raise RuntimeError(f'python-rtmidi missing. Run {sys.executable} -m pip install -r m3/requirements.txt') from error
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11+ required in ~/blemidi')
    if installed != '1.5.8':
        raise RuntimeError(f'Expected python-rtmidi==1.5.8, found {installed}; see m3/requirements.txt')
    sync_files(app, plan)
    wait_for_socket(path, args.wait_seconds)
    print('M3: starting local MIDI-to-LED. Connect MIDI Wrench to toru1; Ctrl+C stops.\n'
          'M3: if connection is refused, Run M3 in App Lab and check for a stale socket.', flush=True)
    os.chdir(REPO)
    os.execv(sys.executable, receiver_args)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nM3: cancelled', file=sys.stderr)
        raise SystemExit(130)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'M3: {error}', file=sys.stderr)
        raise SystemExit(1)
