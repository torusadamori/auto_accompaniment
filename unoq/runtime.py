"""Safe App Lab and relay runtime checks shared by UNO Q tools."""

import json
from pathlib import Path
import socket
import stat
import subprocess


def relay_probe(path: Path, command=b"PING\n", timeout=1.0):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False, "missing"
    if not stat.S_ISSOCK(mode):
        return False, "not a Unix socket"
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(timeout)
            client.connect(str(path))
            client.sendall(command)
            reply = client.recv(16)
        return reply == b"OK\n", f"reply={reply!r}"
    except OSError as error:
        return False, f"{type(error).__name__}: {error}"


def _rows(value):
    if isinstance(value, list):
        yield from (item for item in value if isinstance(item, dict))
    elif isinstance(value, dict):
        for key in ("apps", "result", "data"):
            if key in value:
                yield from _rows(value[key])


def app_lab_status(app: Path):
    """Return (running|stopped|unknown, detail) via the official App CLI."""
    try:
        result = subprocess.run(
            ["arduino-app-cli", "--format", "json", "app", "list"],
            text=True, capture_output=True, timeout=8, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return "unknown", str(error)
    if result.returncode:
        return "unknown", (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
    try:
        rows = list(_rows(json.loads(result.stdout)))
    except (json.JSONDecodeError, TypeError) as error:
        return "unknown", f"unrecognized app list JSON: {error}"
    target, basename = str(app.expanduser().absolute()), app.name.casefold()
    matches = []
    for row in rows:
        values = [str(value) for value in row.values() if isinstance(value, (str, int))]
        if target in values or any(value.casefold() == basename for value in values):
            matches.append(row)
    if not matches:
        return "stopped", f"not listed by App CLI ({len(rows)} app(s) listed)"
    row = matches[0]
    status = str(next((row[key] for key in row if key.casefold() == "status"), "unknown"))
    normalized = status.casefold().strip()
    if normalized in {"running", "starting", "started", "active"}:
        return "running", status
    if normalized in {"stopped", "stop", "exited", "inactive", "created", "idle"}:
        return "stopped", status
    return "unknown", f"status={status}; row={row}"


def remove_stale_socket(path: Path, app: Path, app_stopped=False) -> bool:
    """Remove only an unreachable App-local socket after a confirmed App stop."""
    try:
        parent, resolved_app = path.parent.resolve(strict=True), app.resolve(strict=True)
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if parent != resolved_app:
        raise RuntimeError(f"Relay socket must be directly inside App directory: {path}")
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"Relay path is not a Unix socket and was preserved: {path}")
    reachable, detail = relay_probe(path)
    if reachable:
        return False
    if not app_stopped:
        state, state_detail = app_lab_status(app)
        if state != "stopped":
            raise RuntimeError(
                f"Relay is unreachable ({detail}), but App state is {state} "
                f"({state_detail}); socket preserved: {path}"
            )
    path.unlink(missing_ok=True)
    print(f"UNOQ: removed stale relay socket after confirmed App stop: {path}", flush=True)
    return True


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--cleanup-stale", action="store_true")
    args = parser.parse_args(argv)
    if args.cleanup_stale:
        remove_stale_socket(args.socket.expanduser().absolute(),
                            args.app_dir.expanduser().absolute())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
