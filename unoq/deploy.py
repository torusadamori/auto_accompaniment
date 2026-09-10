"""Safely synchronize repo-owned files into an existing App Lab application."""

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

from .runtime import relay_probe, remove_stale_socket


REPO = Path(__file__).resolve().parents[1]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPO, stderr=subprocess.DEVNULL)


def historical_versions(source_root: Path, relative: str) -> set[bytes]:
    source = (source_root / relative).resolve()
    try:
        repo_relative = source.relative_to(REPO).as_posix()
    except ValueError as error:
        raise RuntimeError(f"Source must be inside the Git repository: {source}") from error
    commits = git("log", "--format=%H", "--", repo_relative).decode().splitlines()
    versions = set()
    for commit in commits:
        try:
            versions.add(git("show", f"{commit}:{repo_relative}"))
        except subprocess.CalledProcessError:
            continue
    return versions


def plan_sync(app: Path, source_root: Path, files: list[str], allow_existing: bool = False,
              history=historical_versions):
    """Validate every destination before returning a write plan."""
    app_yaml = app / "app.yaml"
    if (app.is_symlink() or not app.is_dir() or not app_yaml.is_file()
            or app_yaml.is_symlink()):
        raise RuntimeError(f"Existing App Lab directory with app.yaml required: {app}")
    if source_root.is_symlink() or not source_root.is_dir():
        raise RuntimeError(f"Deployment source directory is missing or a symlink: {source_root}")
    plan = []
    seen = set()
    for relative in files:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts or relative in seen:
            raise RuntimeError(f"Unsafe or duplicate deployment path: {relative}")
        seen.add(relative)
        source = source_root / relative_path
        destination = app / relative_path
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"Regular deployment source required: {source}")
        if (destination.parent.is_symlink() or destination.is_symlink()
                or (destination.exists() and not destination.is_file())):
            raise RuntimeError(f"Refusing non-regular/symlink destination: {destination}")
        content = source.read_bytes()
        old = destination.read_bytes() if destination.exists() else None
        if old == content:
            continue
        if old is not None and not allow_existing and old not in history(source_root, relative):
            raise RuntimeError(
                f"App Lab hand edit preserved: {destination}\n"
                "Inspect with --dry-run --sync-existing, then explicitly use "
                "--sync-existing to replace it after a backup."
            )
        plan.append((destination, content, old))
    return plan


def sync_files(app: Path, plan):
    backup = None
    backup_root = app / ".unoq-backups"
    if backup_root.is_symlink():
        raise RuntimeError(f"Refusing symlink backup directory: {backup_root}")
    if any(old is not None for _, _, old in plan):
        backup = backup_root / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        backup.mkdir(parents=True)
        # Finish every backup before modifying the first destination.
        for destination, _, old in plan:
            if old is not None:
                saved = backup / destination.relative_to(app)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
        print(f"UNOQ deploy: originals backed up to {backup}", flush=True)
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
        print(f"UNOQ deploy: synced {destination}", flush=True)
    return backup


def socket_present(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"Relay path exists but is not a Unix socket (preserved): {path}")
    return True


def stop_running_app(app: Path, relay_socket: Path, wait_seconds: float = 15):
    print("UNOQ deploy: stopping changed App through official arduino-app-cli", flush=True)
    subprocess.run(["arduino-app-cli", "app", "stop", str(app)], check=True)
    deadline = time.monotonic() + wait_seconds
    while socket_present(relay_socket):
        reachable, _ = relay_probe(relay_socket)
        if not reachable:
            remove_stale_socket(relay_socket, app, app_stopped=True)
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"App stop returned but relay process still answers; preserved: {relay_socket}"
            )
        time.sleep(0.25)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--file", action="append", dest="files", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sync-existing", action="store_true")
    parser.add_argument("--stop-running", action="store_true",
                        help="Use official App CLI stop only when changed files must be deployed")
    args = parser.parse_args(argv)
    app = args.app_dir.expanduser().absolute()
    source = args.source_dir.expanduser().absolute()
    relay_socket = args.socket.expanduser().absolute()
    try:
        source.resolve().relative_to(REPO)
    except ValueError as error:
        raise RuntimeError(f"Deployment source must be inside the Git repository: {source}") from error
    plan = plan_sync(app, source, args.files, args.sync_existing)
    for destination, _, old in plan:
        action = "backup + update" if old is not None else "create"
        print(f"UNOQ deploy: {action}: {destination}", flush=True)
    if not plan:
        print("UNOQ deploy: App Lab files already match the repository", flush=True)
        return 0
    if args.dry_run:
        print(f"UNOQ deploy: dry-run; {len(plan)} file(s) would change", flush=True)
        return 0
    if socket_present(relay_socket):
        if args.stop_running:
            stop_running_app(app, relay_socket)
        else:
            raise RuntimeError(
                "App sync is needed while its relay socket exists. Stop the App first, or use "
                "--stop-running; no file or socket was changed."
            )
    sync_files(app, plan)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"UNOQ deploy: FAIL: {error}", file=os.sys.stderr)
        raise SystemExit(1)
