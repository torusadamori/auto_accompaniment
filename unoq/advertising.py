"""Keep the proven bluetoothctl BLE-MIDI advertisement alive for a command."""

import argparse
import queue
import re
import subprocess
import sys
import threading
import time


def command(*args, timeout=5):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout,
                              check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Cannot run {' '.join(args)}: {error}") from error


def controller_state():
    result = command("bluetoothctl", "show")
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).strip() or
                           "bluetoothctl show failed")
    match = re.search(r"ActiveInstances:\s*(?:0x[0-9a-f]+\s*\()?([0-9]+)",
                      result.stdout, re.IGNORECASE)
    # BlueZ commonly prints "0x01 (1)"; the parenthesized decimal is decisive.
    parenthesized = re.search(r"ActiveInstances:.*\(([0-9]+)\)", result.stdout)
    active = int(parenthesized.group(1) if parenthesized else match.group(1)) if match else 0
    return active, result.stdout


def peer_ready(address):
    result = command("bluetoothctl", "info", address)
    text = result.stdout + result.stderr
    return (result.returncode == 0 and "Connected: yes" in text and
            "ServicesResolved: yes" in text), text.strip()


class BluetoothctlAdvertisement:
    """Own `/org/bluez/advertising` for exactly this process lifetime."""

    def __init__(self, service_uuid, local_name="toru1", startup_seconds=10,
                 popen=subprocess.Popen):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", service_uuid):
            raise ValueError(f"Invalid service UUID: {service_uuid!r}")
        if not local_name or any(character in local_name for character in "\r\n"):
            raise ValueError("Local name must be nonempty and contain no newlines")
        self.service_uuid = service_uuid.lower()
        self.local_name = local_name
        self.startup_seconds = startup_seconds
        self.popen = popen
        self.process = None
        self.owned = False
        self.output = queue.Queue()

    def _read_output(self):
        for line in self.process.stdout:
            text = line.rstrip()
            if text:
                print(f"UNOQ advertising: {text}", flush=True)
                self.output.put(text)

    def start(self):
        active, show = controller_state()
        if self.service_uuid not in show.lower():
            raise RuntimeError(
                f"Local GATT service {self.service_uuid} is not registered. "
                "Advertising alone cannot replace it; run doctor.sh to identify its owner."
            )
        if active:
            print(f"UNOQ advertising: reusing {active} existing BlueZ instance(s)", flush=True)
            return
        self.process = self.popen(
            ["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        threading.Thread(target=self._read_output, daemon=True).start()
        # This is the same bluetoothctl/LEAdvertisingManager1 mechanism recorded
        # in the successful experiment. The desired board name lives in the
        # UNO Q config rather than being scattered through the launcher.
        commands = (
            "menu advertise",
            f"uuids {self.service_uuid}",
            f"name {self.local_name}",
            "discoverable on",
            "timeout 0",
            "back",
            "advertise peripheral",
        )
        for item in commands:
            self.process.stdin.write(item + "\n")
        self.process.stdin.flush()
        try:
            deadline = time.monotonic() + self.startup_seconds
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"bluetoothctl advertising owner exited: {self.process.returncode}")
                active, _ = controller_state()
                if active:
                    self.owned = True
                    print(f"UNOQ advertising: PASS ActiveInstances={active}; "
                          "owner=bluetoothctl", flush=True)
                    return
                try:
                    line = self.output.get(timeout=0.2)
                    if "failed" in line.casefold():
                        raise RuntimeError(line)
                except queue.Empty:
                    pass
            raise RuntimeError(
                "Advertising did not create a BlueZ ActiveInstance before timeout")
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                if self.owned:
                    self.process.stdin.write("advertise off\n")
                    self.process.stdin.flush()
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        self.process = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


def wait_for_peer(address, seconds):
    deadline = time.monotonic() + seconds
    announced = False
    while True:
        ready, detail = peer_ready(address)
        if ready:
            print(f"UNOQ advertising: peer connected and services resolved: {address}", flush=True)
            return
        if not announced:
            print("UNOQ advertising: iPhone MIDI Wrenchで toru1 を選択してください。"
                  f" Waiting up to {seconds:g}s.", flush=True)
            announced = True
        if time.monotonic() >= deadline:
            raise RuntimeError(f"iPhone peer did not connect/resolve: {address}; last={detail}")
        time.sleep(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-uuid", required=True)
    parser.add_argument("--local-name", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--wait-seconds", type=float, default=120)
    parser.add_argument("--startup-seconds", type=float, default=10)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    child = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not child:
        parser.error("a receiver command is required after --")
    advertisement = BluetoothctlAdvertisement(
        args.service_uuid, args.local_name, args.startup_seconds)
    try:
        advertisement.start()
        wait_for_peer(args.address, args.wait_seconds)
        return subprocess.call(child)
    finally:
        advertisement.stop()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"UNOQ advertising: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
