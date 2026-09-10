"""Non-destructive UNO Q BLE-MIDI, App Lab, relay, and Git diagnostics."""

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import queue
import subprocess
import sys

from m3.midi_input import OpenMidiInput, list_input_ports, select_input_port
from .runtime import app_lab_status, relay_probe


class Report:
    def __init__(self):
        self.results = []

    def add(self, level: str, name: str, detail: str):
        self.results.append((level, name, detail))
        print(f"{level:<4} {name:<28} {detail}")

    def exit_code(self) -> int:
        return 1 if any(level == "FAIL" for level, _, _ in self.results) else 0

    def summary(self):
        counts = {level: sum(item[0] == level for item in self.results)
                  for level in ("PASS", "WARN", "FAIL")}
        print(f"---- summary: PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}")


def command(*args: str, timeout: float = 5):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return None, str(error)
    return result.returncode, (result.stdout + result.stderr).strip()


def inspect_bluetooth(report: Report):
    code, output = command("bluetoothctl", "--version")
    report.add("PASS" if code == 0 else "FAIL", "BlueZ", output or "bluetoothctl unavailable")
    code, output = command("bluetoothctl", "show")
    if code != 0:
        report.add("FAIL", "Bluetooth adapter", output or "no default adapter")
    elif "Powered: yes" in output:
        alias = next((line.strip() for line in output.splitlines() if "Alias:" in line), "")
        report.add("PASS", "Bluetooth adapter", f"powered; {alias}".rstrip("; "))
    else:
        report.add("FAIL", "Bluetooth adapter", "present but not powered")
    code, output = command("bluetoothctl", "devices", "Connected")
    if code == 0 and output:
        report.add("PASS", "BLE connected peer", output.replace("\n", "; "))
    elif code == 0:
        report.add("WARN", "BLE connected peer",
                   "BlueZ reports none (some BLE-MIDI servers expose only a MIDI port)")
    else:
        report.add("WARN", "BLE connected peer", output or "status unavailable")


def inspect_pipewire(report: Report):
    code, output = command("pw-dump", timeout=8)
    if code != 0:
        alsa = Path("/dev/snd/seq").exists()
        report.add("PASS" if alsa else "WARN", "BLE-MIDI provider",
                   "pw-dump unavailable; ALSA sequencer present" if alsa else
                   "pw-dump unavailable and /dev/snd/seq missing")
        return
    try:
        objects = json.loads(output)
    except json.JSONDecodeError as error:
        report.add("WARN", "BLE-MIDI provider", f"pw-dump JSON invalid: {error}")
        return
    matches = []
    for item in objects if isinstance(objects, list) else []:
        props = item.get("info", {}).get("props", {}) if isinstance(item, dict) else {}
        text = " ".join(str(value) for value in props.values())
        if ("midi" in text.casefold() and
                any(word in text.casefold() for word in ("bluez", "ble", "toru1"))):
            matches.append(props.get("node.description") or props.get("node.name") or text[:120])
    if matches:
        report.add("PASS", "BLE-MIDI provider", "; ".join(dict.fromkeys(matches)))
    else:
        report.add("WARN", "BLE-MIDI provider",
                   "PipeWire is reachable, but no BlueZ/BLE MIDI object is currently visible")


def inspect_midi(pattern: str, seconds: float, report: Report):
    try:
        ports = list_input_ports()
        selected = select_input_port(ports, pattern)
    except Exception as error:
        report.add("FAIL", "Linux MIDI input", f"{type(error).__name__}: {error}")
        report.add("WARN", "non-destructive MIDI probe", "not attempted")
        return
    detail = "; ".join(port.label for port in ports) or "no input ports"
    if selected is None:
        report.add("FAIL", "Linux MIDI input", f"no match for {pattern!r}; available: {detail}")
        report.add("WARN", "non-destructive MIDI probe", "no matching port to open")
        return
    report.add("PASS", "Linux MIDI input", f"selected {selected.label}; available: {detail}")
    events = queue.Queue()
    try:
        with OpenMidiInput(selected, lambda _delta, data: events.put(bytes(data))):
            try:
                message = events.get(timeout=seconds) if seconds else None
            except queue.Empty:
                message = None
        if message is None:
            report.add("PASS", "non-destructive MIDI probe",
                       f"opened {selected.label} alongside other clients; no event in {seconds:g}s")
        else:
            report.add("PASS", "non-destructive MIDI probe",
                       f"received {message.hex(' ')} without sending MIDI or changing LED")
    except Exception as error:
        report.add("WARN", "non-destructive MIDI probe",
                   f"port found but parallel open failed: {type(error).__name__}: {error}")


def inspect_relay(path: Path, report: Report):
    reachable, detail = relay_probe(path, b"PROBE\n", 2)
    if detail == "missing":
        report.add("WARN", "relay socket", f"not present: {path}")
        report.add("WARN", "Bridge relay connectivity", "App is not running")
    elif detail == "not a Unix socket":
        report.add("FAIL", "relay socket", f"non-socket path preserved: {path}")
        report.add("FAIL", "Bridge relay connectivity", "not attempted")
    else:
        report.add("PASS", "relay socket", str(path))
        report.add("PASS" if reachable else "FAIL", "Bridge relay connectivity",
                   "PROBE acknowledged without changing LED" if reachable else detail)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--port-pattern", required=True)
    parser.add_argument("--midi-probe-seconds", type=float, default=1)
    args = parser.parse_args(argv)
    report = Report()

    report.add("PASS" if sys.version_info >= (3, 11) else "FAIL", "Python",
               f"{sys.version.split()[0]} at {sys.executable}")
    venv = args.venv.expanduser()
    report.add("PASS" if (venv / "pyvenv.cfg").is_file() and (venv / "bin/python").is_file()
               else "FAIL", "venv", str(venv))
    try:
        installed = version("python-rtmidi")
        report.add("PASS" if installed == "1.5.8" else "WARN", "python-rtmidi", installed)
    except PackageNotFoundError:
        report.add("FAIL", "python-rtmidi", "not installed; run setup.sh")

    inspect_bluetooth(report)
    inspect_pipewire(report)
    inspect_midi(args.port_pattern, max(0, args.midi_probe_seconds), report)
    app = args.app_dir.expanduser()
    report.add("PASS" if app.is_dir() and (app / "app.yaml").is_file() else "FAIL",
               "App Lab directory", str(app))
    state, detail = app_lab_status(app)
    report.add("PASS" if state == "running" else "WARN", "App Lab App state",
               f"{state}: {detail}")
    inspect_relay(args.socket.expanduser(), report)

    code, output = command("git", "status", "--short", "--branch", timeout=10)
    if code != 0:
        report.add("FAIL", "Git status", output or "git status failed")
    else:
        dirty = any(line and not line.startswith("##") for line in output.splitlines())
        report.add("WARN" if dirty else "PASS", "Git status", output.replace("\n", "; "))
    report.summary()
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
