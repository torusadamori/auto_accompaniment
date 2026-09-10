"""Non-destructive UNO Q host, Bluetooth, App Lab, relay, and Git diagnostics."""

import argparse
import asyncio
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import socket
import stat
import subprocess
import sys

from .runtime import app_lab_status


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
                      timeout: float, notify_seconds: float, report: Report):
    try:
        from bleak import BleakClient
    except ImportError as error:
        report.add("FAIL", "BLE device", f"Bleak unavailable: {error}")
        report.add("FAIL", "BLE MIDI service UUID", "not checked")
        report.add("FAIL", "BLE MIDI characteristic UUID", "not checked")
        report.add("FAIL", "BLE notify path", "not checked")
        return
    packets = []
    connected = False
    try:
        # This is the proven experiment path: use the known BlueZ peer object
        # directly. A connected peer need not appear in a fresh advertisement scan.
        async with BleakClient(address, timeout=timeout) as client:
            connected = True
            report.add("PASS", "BLE device", f"BleakClient connected to BlueZ peer {address}")
            service = client.services.get_service(service_uuid)
            if service is None:
                report.add("FAIL", "BLE MIDI service UUID", f"missing {service_uuid}")
                report.add("FAIL", "BLE MIDI characteristic UUID", "service missing")
                report.add("FAIL", "BLE notify path", "service missing")
                return
            report.add("PASS", "BLE MIDI service UUID", service_uuid)
            characteristic = service.get_characteristic(characteristic_uuid)
            if characteristic is None:
                report.add("FAIL", "BLE MIDI characteristic UUID", f"missing {characteristic_uuid}")
                report.add("FAIL", "BLE notify path", "characteristic missing")
            elif "notify" not in characteristic.properties:
                report.add("FAIL", "BLE MIDI characteristic UUID", "found, but notify is unavailable")
                report.add("FAIL", "BLE notify path", "notify property missing")
            else:
                report.add("PASS", "BLE MIDI characteristic UUID", f"{characteristic_uuid} (notify)")
                await client.start_notify(characteristic, lambda _sender, data: packets.append(bytes(data)))
                await asyncio.sleep(notify_seconds)
                await client.stop_notify(characteristic)
                detail = (f"subscribed; received {len(packets)} packet(s)" if packets else
                          f"subscription succeeded; no packet during {notify_seconds:g}s probe")
                report.add("PASS", "BLE notify path", detail)
    except Exception as error:
        if connected:
            report.add("WARN", "BLE notify path",
                       f"subscription probe failed: {type(error).__name__}: {error}; "
                       "stop another BLE monitor if it owns notify")
        else:
            report.add("WARN", "BLE device",
                       f"direct connection failed: {type(error).__name__}: {error}")
            report.add("WARN", "BLE MIDI service UUID", "not checked")
            report.add("WARN", "BLE MIDI characteristic UUID", "not checked")
            report.add("WARN", "BLE notify path", "not checked")


def inspect_advertising(show: str, service_uuid: str, report: Report):
    match = re.search(r"ActiveInstances:.*\(([0-9]+)\)", show)
    if not match:
        match = re.search(r"ActiveInstances:\s*([0-9]+)", show)
    active = int(match.group(1)) if match else 0
    report.add("PASS" if active else "FAIL", "BLE advertising instance",
               f"ActiveInstances={active}" if active else
               "ActiveInstances=0; iPhone cannot discover toru1")
    report.add("PASS" if service_uuid.lower() in show.lower() else "FAIL",
               "local BLE MIDI GATT UUID",
               "registered on adapter" if service_uuid.lower() in show.lower() else
               f"missing from adapter: {service_uuid}")

    code, output = command("busctl", "--system", "introspect", "org.bluez",
                           "/org/bluez/hci0", timeout=5)
    if code == 0:
        for interface in ("org.bluez.LEAdvertisingManager1", "org.bluez.GattManager1"):
            report.add("PASS" if interface in output else "FAIL", interface.rsplit(".", 1)[-1],
                       "available on /org/bluez/hci0" if interface in output else "missing")
    else:
        report.add("WARN", "BlueZ manager interfaces", output or "busctl unavailable")

    code, listing = command("busctl", "--system", "list", "--no-legend", "--no-pager",
                            timeout=5)
    if code != 0:
        report.add("WARN", "Advertising object owner", listing or "busctl list unavailable")
        report.add("WARN", "GATT application owner", "not inspected")
        return
    advertisers, gatt_apps = [], []
    for line in listing.splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith(":"):
            continue
        owner = fields[0]
        code, tree = command("busctl", "--system", "tree", owner, "--no-pager", timeout=2)
        if code != 0:
            continue
        if "/org/bluez/advertising" in tree:
            advertisers.append(" ".join(fields[:4]))
        paths = re.findall(r"(/[^\s]+)", tree)
        for path in paths:
            if not any(word in path.casefold() for word in ("service", "gatt", "midi")):
                continue
            code, details = command("busctl", "--system", "introspect", owner, path,
                                    timeout=2)
            if code == 0 and ("org.bluez.GattService1" in details or
                              "org.bluez.GattCharacteristic1" in details):
                gatt_apps.append(f"{' '.join(fields[:4])} path={path}")
                break
    report.add("PASS" if advertisers else ("FAIL" if active else "WARN"),
               "Advertising object owner",
               "; ".join(advertisers) if advertisers else "no exported /org/bluez/advertising owner")
    report.add("PASS" if gatt_apps else "WARN", "GATT application owner",
               "; ".join(gatt_apps) if gatt_apps else
               "not identifiable from exported D-Bus object paths")


def inspect_services(report: Report):
    relevant = []
    for scope in ("system", "user"):
        args = ["systemctl"] + (["--user"] if scope == "user" else []) + [
            "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager"]
        code, output = command(*args, timeout=8)
        if code == 0:
            relevant.extend(f"{scope}:{line.strip()}" for line in output.splitlines()
                            if any(word in line.casefold()
                                   for word in ("bluetooth", "bluez", "midi", "app-lab", "arduino")))
    report.add("PASS" if relevant else "WARN", "BLE-related services",
               "; ".join(relevant) if relevant else "no matching running system/user unit")


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
    parser.add_argument("--notify-probe-seconds", type=float, default=1)
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
        inspect_advertising(output, args.service_uuid, report)
    else:
        report.add("FAIL", "Bluetooth adapter", "present but not powered")
    inspect_services(report)

    code, output = command("bluetoothctl", "info", args.address)
    if code == 0:
        facts = [line.strip() for line in output.splitlines()
                 if any(key in line for key in ("Name:", "Connected:", "ServicesResolved:"))]
        connected = "Connected: yes" in output and "ServicesResolved: yes" in output
        report.add("PASS" if connected else "WARN", "BlueZ iPhone peer",
                   "; ".join(facts) or f"known peer {args.address}")
    else:
        report.add("WARN", "BlueZ iPhone peer", output or f"peer unavailable: {args.address}")

    asyncio.run(inspect_ble(args.address, args.service_uuid.lower(),
                            args.characteristic_uuid.lower(), args.ble_timeout,
                            max(0, args.notify_probe_seconds), report))

    app = args.app_dir.expanduser()
    report.add("PASS" if app.is_dir() and (app / "app.yaml").is_file() else "FAIL",
               "App Lab directory", str(app))
    code, output = command("arduino-app-cli", "version")
    if code is None or code != 0:
        code, output = command("arduino-app-cli", "--version")
    report.add("PASS" if code == 0 else "WARN", "Arduino App CLI",
               output or "not available; GUI Run fallback can be configured")
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
