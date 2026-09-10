"""Non-destructive UNO Q host, Bluetooth, App Lab, relay, and Git diagnostics."""

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import socket
import stat
import subprocess
import sys


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
    text = (result.stdout + result.stderr).strip()
    return result.returncode, text


async def inspect_ble(address: str, service_uuid: str, characteristic_uuid: str,
                      timeout: float, report: Report):
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as error:
        report.add("FAIL", "BLE device", f"Bleak unavailable: {error}")
        report.add("FAIL", "BLE MIDI service UUID", "not checked")
        report.add("FAIL", "BLE MIDI characteristic UUID", "not checked")
        return
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    except Exception as error:
        report.add("WARN", "BLE device", f"scan failed: {type(error).__name__}: {error}")
        report.add("WARN", "BLE MIDI service UUID", "not checked")
        report.add("WARN", "BLE MIDI characteristic UUID", "not checked")
        return
    if device is None:
        report.add("WARN", "BLE device", f"not advertising: {address}; ready the iPhone app")
        report.add("WARN", "BLE MIDI service UUID", "not checked")
        report.add("WARN", "BLE MIDI characteristic UUID", "not checked")
        return
    report.add("PASS", "BLE device", f"found {device.address} {device.name or ''}".rstrip())
    try:
        async with BleakClient(device, timeout=timeout) as client:
            service = client.services.get_service(service_uuid)
            if service is None:
                report.add("FAIL", "BLE MIDI service UUID", f"missing {service_uuid}")
                report.add("FAIL", "BLE MIDI characteristic UUID", "service missing")
                return
            report.add("PASS", "BLE MIDI service UUID", service_uuid)
            characteristic = service.get_characteristic(characteristic_uuid)
            if characteristic is None:
                report.add("FAIL", "BLE MIDI characteristic UUID", f"missing {characteristic_uuid}")
            elif "notify" not in characteristic.properties:
                report.add("FAIL", "BLE MIDI characteristic UUID", "found, but notify is unavailable")
            else:
                report.add("PASS", "BLE MIDI characteristic UUID", f"{characteristic_uuid} (notify)")
    except Exception as error:
        report.add("WARN", "BLE MIDI service UUID", f"connection failed: {type(error).__name__}: {error}")
        report.add("WARN", "BLE MIDI characteristic UUID", "not checked")


def inspect_relay(path: Path, report: Report):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        report.add("WARN", "relay socket", f"not present: {path}")
        report.add("WARN", "Bridge relay connectivity", "App is not running")
        return
    if not stat.S_ISSOCK(mode):
        report.add("FAIL", "relay socket", f"path is not a socket (preserved): {path}")
        report.add("FAIL", "Bridge relay connectivity", "not attempted")
        return
    report.add("PASS", "relay socket", str(path))
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(str(path))
            client.sendall(b"PROBE\n")
            reply = client.recv(16)
        if reply == b"OK\n":
            report.add("PASS", "Bridge relay connectivity", "Bridge RPC acknowledged without changing LED state")
        else:
            report.add("WARN", "Bridge relay connectivity", "listener reachable but busy or lacks PROBE support")
    except OSError as error:
        report.add("FAIL", "Bridge relay connectivity", f"{type(error).__name__}: {error}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--service-uuid", required=True)
    parser.add_argument("--characteristic-uuid", required=True)
    parser.add_argument("--ble-timeout", type=float, default=10)
    args = parser.parse_args(argv)
    report = Report()

    report.add("PASS" if sys.version_info >= (3, 11) else "FAIL", "Python",
               f"{sys.version.split()[0]} at {sys.executable}")
    venv = args.venv.expanduser()
    venv_python = venv / "bin/python"
    report.add("PASS" if (venv / "pyvenv.cfg").is_file() and venv_python.is_file() else "FAIL",
               "venv", str(venv))
    try:
        bleak_version = version("bleak")
        report.add("PASS" if bleak_version == "3.0.2" else "WARN", "bleak", bleak_version)
    except PackageNotFoundError:
        report.add("FAIL", "bleak", "not installed in configured venv")

    code, output = command("bluetoothctl", "--version")
    report.add("PASS" if code == 0 else "FAIL", "BlueZ", output or "bluetoothctl unavailable")
    code, output = command("bluetoothctl", "show")
    if code != 0:
        report.add("FAIL", "Bluetooth adapter", output or "no default adapter")
    elif "Powered: yes" in output:
        report.add("PASS", "Bluetooth adapter", "present and powered")
    else:
        report.add("FAIL", "Bluetooth adapter", "present but not powered")

    asyncio.run(inspect_ble(args.address, args.service_uuid.lower(),
                            args.characteristic_uuid.lower(), args.ble_timeout, report))

    app = args.app_dir.expanduser()
    report.add("PASS" if app.is_dir() and (app / "app.yaml").is_file() else "FAIL",
               "App Lab directory", str(app))
    code, output = command("arduino-app-cli", "version")
    if code is None or code != 0:
        code, output = command("arduino-app-cli", "--version")
    report.add("PASS" if code == 0 else "WARN", "Arduino App CLI",
               output or "not available; GUI Run fallback can be configured")
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
