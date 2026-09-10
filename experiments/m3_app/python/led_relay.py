"""Local-only, single-owner LED relay. No Arduino dependency in this module."""

import logging
from pathlib import Path
import select
import socket
import time

LOG = logging.getLogger("m3.relay")


class LedRelay:
    def __init__(self, set_led, port=8765, idle_timeout=3, unix_path=None):
        self.set_led = set_led
        self.idle_timeout = idle_timeout
        self.client = None
        self.observers = {}
        self.client_controls_output = False
        self.pending = bytearray()
        self.last_command = 0
        self.state = None
        self.unix_path = Path(unix_path) if unix_path else None
        self.listener = socket.socket(socket.AF_UNIX if unix_path else socket.AF_INET)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.listener.bind(str(self.unix_path) if self.unix_path else ("127.0.0.1", port))
        except BaseException:
            self.listener.close()
            raise
        if self.unix_path:
            self.unix_path.chmod(0o600)
        self.listener.listen(1)
        self.listener.setblocking(False)

    def _set(self, state):
        if self.state != state:
            self.set_led(state)  # Synchronous RPC; ACK only after completion.
            self.state = state
            LOG.info("LED %s (Bridge acknowledged)", "ON" if state else "OFF")

    def disconnect(self):
        controlled_output = self.client_controls_output
        if self.client:
            self.client.close()
            self.client = None
        self.client_controls_output = False
        self.pending.clear()
        if controlled_output:
            # Force an OFF RPC even after an ON call with an uncertain result.
            self.state = None
            self._set(False)

    def step(self):
        if self.state is None:
            self._set(False)
            LOG.info("M3 relay listening on %s", self.listener.getsockname())
        if self.client and time.monotonic() - self.last_command > self.idle_timeout:
            LOG.warning("Host heartbeat expired")
            self.disconnect()
        sockets = [self.listener] + ([self.client] if self.client else []) + list(self.observers)
        ready, _, _ = select.select(sockets, [], [], 0.1)
        if self.listener in ready:
            newcomer, _ = self.listener.accept()
            if self.client:
                # A concurrent doctor/PING connection may observe health but can
                # never become output owner or affect the owner's heartbeat.
                newcomer.setblocking(False)
                self.observers[newcomer] = bytearray()
            else:
                self.client = newcomer
                self.client_controls_output = False
                self.client.settimeout(1)
                self.last_command = time.monotonic()
        for observer in set(ready) & self.observers.keys():
            try:
                data = observer.recv(64)
                pending = self.observers[observer]
                pending.extend(data)
                if b"\n" not in pending:
                    if len(pending) <= 6 and data:
                        continue
                    raise ValueError("Invalid diagnostic request")
                frame = bytes(pending).partition(b"\n")[0]
                if frame == b"PING":
                    observer.sendall(b"OK\n")
                elif frame == b"PROBE":
                    self.set_led(bool(self.state))
                    observer.sendall(b"OK\n")
                else:
                    raise ValueError("Second connection may only PING or PROBE")
            except (BlockingIOError, InterruptedError):
                continue
            except (OSError, ValueError):
                LOG.debug("Diagnostic relay client closed", exc_info=True)
                observer.close()
                del self.observers[observer]
            finally:
                if observer in self.observers and b"\n" in self.observers[observer]:
                    observer.close()
                    del self.observers[observer]
        if self.client not in ready:
            return
        try:
            data = self.client.recv(64)
            if not data:
                self.disconnect()
                return
            self.pending.extend(data)
            while b"\n" in self.pending:
                frame, _, rest = self.pending.partition(b"\n")
                self.pending[:] = rest
                if frame == b"PING":
                    self.last_command = time.monotonic()
                    self.client.sendall(b"OK\n")
                    continue
                if frame == b"PROBE":
                    # Exercise the real Bridge RPC without changing logical state.
                    self.set_led(bool(self.state))
                    self.last_command = time.monotonic()
                    self.client.sendall(b"OK\n")
                    continue
                if frame not in (b"0", b"1"):
                    raise ValueError("Expected PING, PROBE, 0 or 1 followed by LF")
                self.client_controls_output = True
                self._set(frame == b"1")
                self.last_command = time.monotonic()
                self.client.sendall(b"OK\n")
            if len(self.pending) > 5:
                raise ValueError("Oversized command")
        except (OSError, ValueError):
            LOG.exception("Relay client failed")
            self.disconnect()
        except Exception:
            self.disconnect()
            raise

    def close(self):
        try:
            self.disconnect()
            for observer in self.observers:
                observer.close()
            self.observers.clear()
        finally:
            self.listener.close()
            if self.unix_path:
                self.unix_path.unlink(missing_ok=True)
