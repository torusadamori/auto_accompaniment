# Experiment Log — 2026-09-10

## Purpose

This document records the hands-on experiments performed on Arduino UNO Q 2GB for the `auto_accompaniment` project.

Only observations that were actually confirmed on hardware are listed as confirmed results. Architectural interpretations and next steps are separated from measured facts.

---

## 1. Test environment

- Board: Arduino UNO Q 2GB
- Board name: `toru1`
- Arduino App Lab: 0.10.0
- MCU: STM32U585
- Linux side: Debian-based UNO Q Linux environment
- BLE peer: iPhone
- BLE MIDI service UUID: `03b80e5a-ede8-4b33-a751-6ce34ec4c700`
- BLE MIDI characteristic UUID: `7772e5db-3868-4112-a1a9-f2669d106bf3`

---

## 2. Arduino App Lab initial setup

### Result: PASS

Initial board configuration, network setup and Linux credentials were completed.

App Lab initially appeared unable to advance after `Board configuration set successfully`, but restarting App Lab resolved the issue and the main App Lab screen became available.

The board appeared as:

```text
toru1
Arduino UNO Q
```

---

## 3. Linux -> Router Bridge -> STM32 -> LED

### Test

Arduino App Lab example `Blink LED from Python` was executed.

The example architecture was:

```text
Python timer loop
    -> Router Bridge
    -> STM32 Arduino sketch
    -> built-in LED
```

### Result: PASS

The red built-in LED blinked successfully.

This confirms that the following path works on the test board:

```text
UNO Q Linux / Python
    -> Arduino Router Bridge
    -> STM32U585
    -> physical GPIO output
```

---

## 4. STM32 value returned to Linux via Bridge

### Test

A copied App Lab project named `MCU to Linux Bridge` was created.

STM32 sketch incremented a counter every 100 ms and exposed a `get_counter` RPC function.

Linux/Python called `get_counter` every 500 ms.

Observed output included:

```text
MCU counter = 51
MCU counter = 56
MCU counter = 61
MCU counter = 66
...
MCU counter = 376
MCU counter = 382
MCU counter = 387
```

### Result: PASS

The returned value increased by approximately five counts for every Python read, as expected from the 100 ms MCU update / 500 ms Python polling intervals.

This confirms that values originating on the STM32 side can be returned to Linux through Router Bridge RPC.

Note: this test used Linux-initiated RPC with an MCU return value. It was not an unsolicited MCU push test.

---

## 5. Router Bridge RTT benchmark

### Test

Linux/Python called an STM32 `echo_value()` RPC 1000 times.

Each call sent an integer value and STM32 immediately returned the same value.

Timing was measured on Linux using `time.perf_counter_ns()` around the complete `Bridge.call()`.

### Result: PASS

Measured round-trip time (RTT):

| Metric | RTT |
|---|---:|
| Samples | 1000 |
| Mean | 7.133 ms |
| Median | 7.226 ms |
| p95 | 7.808 ms |
| p99 | 8.102 ms |
| Minimum | 5.365 ms |
| Maximum | 23.098 ms |
| > 5 ms | 1000 |
| > 10 ms | 1 |
| > 20 ms | 1 |
| > 50 ms | 0 |
| > 100 ms | 0 |

### Interpretation

Under this light RPC workload, Router Bridge latency was highly concentrated around 7–8 ms.

A single outlier of about 23 ms occurred in 1000 calls.

This is encouraging for the UNO Q single-board architecture, but it does not yet prove behavior under simultaneous BLE traffic, touch input and scheduled output load.

For production timing, queued/scheduled events on the MCU are still preferred over relying on one just-in-time RPC call per physical note event.

---

## 6. Bluetooth controller availability

### Test

On UNO Q Linux:

```bash
bluetoothctl
show
```

### Result: PASS

The UNO Q Bluetooth controller was detected and powered.

Observed capabilities included:

```text
Powered: yes
Pairable: yes
Roles: central
Roles: peripheral
```

The controller supported LE advertising instances.

---

## 7. BLE advertising

### Test

Using `bluetoothctl`, an advertisement was configured with:

```text
LocalName: UNOQ-MIDI
UUID: 03b80e5a-ede8-4b33-a751-6ce34ec4c700
```

The advertisement was enabled.

### Result: PASS

Observed output:

```text
Advertising object registered
ActiveInstances: 0x01 (1)
LocalName: UNOQ-MIDI
UUID: Vendor specific(03b80e5a-ede8-4b33-a751-6ce34ec4c700)
Discoverable: on
```

The iPhone was able to connect.

Important: advertising a BLE MIDI service UUID alone is not equivalent to implementing a BLE MIDI GATT server. This experiment only confirmed BLE advertising and connectivity.

---

## 8. iPhone pairing / BLE connection

### Result: PASS

The iPhone successfully paired/bonded with the UNO Q.

Observed status included:

```text
Bonded: yes
Paired: yes
Connected: yes
ServicesResolved: yes
```

---

## 9. BLE MIDI GATT service discovery

### Test

After connection, `bluetoothctl` GATT attributes were listed.

### Result: PASS

The iPhone exposed the standard BLE MIDI service:

```text
Primary Service
03b80e5a-ede8-4b33-a751-6ce34ec4c700
```

and the BLE MIDI characteristic:

```text
Characteristic
7772e5db-3868-4112-a1a9-f2669d106bf3
```

Characteristic flags included:

```text
read
write-without-response
notify
extended-properties
reliable-write
```

MTU reported by `bluetoothctl`:

```text
0x0205 (517)
```

---

## 10. BLE MIDI notification reception with bluetoothctl

### Test

The BLE MIDI characteristic was selected and notification enabled:

```text
notify on
```

### Result: PASS

Observed:

```text
Notifying: yes
Notify started
```

Raw BLE MIDI packets were then received from the iPhone.

Examples:

```text
a3 c9 90 2d 29

a3 d9 80 2d 29 d9 90 2f 29

a4 bd 80 39 29 bd 90 3c 29
```

These include standard MIDI messages such as:

```text
90 nn vv  -> Note On
80 nn vv  -> Note Off
```

inside BLE MIDI packets with timestamp bytes.

This confirms:

```text
iPhone
  -> BLE MIDI
  -> UNO Q Bluetooth / BlueZ
  -> GATT notification
  -> raw MIDI packet visible on Linux
```

---

## 11. Full song BLE MIDI reception: `Tulip`

### Test

The song `Tulip` was transmitted from the iPhone.

### Result: PASS

The UNO Q received a continuous stream of BLE MIDI packets.

Examples included:

```text
90 3c 50   # C4 Note On, velocity 80
80 3c 00   # C4 Note Off
90 3e 50   # D4 Note On, velocity 80
90 40 50   # E4 Note On, velocity 80
90 43 50   # G4 Note On, velocity 80
```

Additional lower notes such as:

```text
90 30 50   # C3 Note On
```

were also present in some packets, showing that the source stream may contain accompaniment/bass or multiple simultaneous note events in addition to melody.

The stream also contained:

- Control Change messages (`B0` etc.)
- Program Change (`C0`)
- SysEx data
- multi-message BLE packets
- All Notes Off (`CC 123`) across MIDI channels near song end
- Reset All Controllers (`CC 121`) in some sequences

Therefore the final BLE MIDI parser must not assume one Note On/Off event per BLE notification.

---

## 12. ALSA MIDI sequencer test

### Test

```bash
aconnect -l
```

### Result: NOT AVAILABLE IN CURRENT IMAGE

Observed:

```text
ALSA lib seq_hw.c:540:(snd_seq_hw_open) open /dev/snd/seq failed: No such file or directory
can't open sequencer
```

The current UNO Q Linux environment does not expose `/dev/snd/seq` in this configuration, so the current experiments proceeded through BlueZ/GATT directly instead of ALSA Sequencer.

This is not a BLE MIDI failure.

---

## 13. Python / Bleak setup on UNO Q host Linux

### Test

The stock Linux Python environment did not initially include `venv`, `pip` or Bleak.

Installed:

```bash
sudo apt install -y python3.13-venv python3-pip
```

Then created:

```bash
python3 -m venv ~/blemidi
source ~/blemidi/bin/activate
python -m pip install --upgrade pip
python -m pip install bleak
```

Installed versions observed:

```text
bleak 3.0.2
dbus-fast 5.0.22
```

### Result: PASS

A host-side Python virtual environment was successfully created and Bleak was installed.

---

## 14. Python / Bleak BLE MIDI reception

### Test

A host Linux script `~/blemidi/ble_midi_monitor.py` used Bleak to connect to the iPhone and subscribe to:

```text
7772e5db-3868-4112-a1a9-f2669d106bf3
```

The first version failed because pasted non-UTF-8 characters were present in the Python file. An ASCII-only version was then used.

### Result: PASS

Python successfully received BLE MIDI notifications and decoded Note On / Note Off events.

Example output:

```text
NOTE ON  ch=1 note=83 B5 vel=24
NOTE OFF ch=1 note=83 B5 vel=24
NOTE ON  ch=1 note=82 A#5 vel=24
...
```

During `Tulip` playback, examples included:

```text
NOTE ON  ch=1 note=60 C4 vel=80
NOTE OFF ch=1 note=60 C4 vel=0
NOTE ON  ch=1 note=62 D4 vel=80
NOTE ON  ch=1 note=64 E4 vel=80
NOTE ON  ch=1 note=67 G4 vel=80
```

The experimental parser also printed raw BLE packets and identified non-note status bytes as `OTHER MIDI`.

This confirms the following complete path without `bluetoothctl` as the data monitor:

```text
iPhone BLE MIDI
    -> BlueZ on UNO Q Linux
    -> Bleak Python client
    -> BLE MIDI characteristic notifications
    -> Python Note On / Note Off decoding
```

---

## 15. What is now confirmed

The following major building blocks have been demonstrated on real hardware:

1. UNO Q Linux/Python can command STM32 through Router Bridge.
2. STM32 can return data to Linux through RPC.
3. Light-load Router Bridge RTT is approximately 7–8 ms with rare larger outliers.
4. UNO Q Bluetooth works as BLE central/peripheral at the BlueZ level.
5. iPhone can pair/connect with the UNO Q.
6. BLE MIDI service and characteristic can be discovered.
7. BLE MIDI notifications can be received by `bluetoothctl`.
8. Full song MIDI traffic can be received over BLE.
9. Host Linux Python + Bleak can receive BLE MIDI directly.
10. Note On / Note Off events can be decoded in Python.

These results materially reduce the technical risk of using Arduino UNO Q 2GB as the main controller for the project.

---

## 16. Not yet confirmed

The following remain untested or incomplete:

- BLE MIDI reception from inside an App Lab container
- host-Linux BLE receiver -> App Lab process communication
- host-Linux BLE receiver -> STM32 Bridge control in one integrated application
- continuous bidirectional Bridge load while BLE MIDI is active
- 10 / 50 / 100 Hz loaded Bridge benchmarks
- event batching and scheduled MCU queue execution
- precise Linux/STM32 monotonic time synchronization strategy
- actual MCP23017 accordion solenoid control from UNO Q
- solenoid fail-safe / maximum-on-time implementation
- touch-bar hardware and sensing topology
- touch latency under simultaneous BLE traffic
- physical MIDI output to Yamaha MU80
- Jazz Engine implementation
- melody/accompaniment channel separation
- correct full BLE MIDI parser including Running Status, SysEx and all relevant channel/system messages
- fixed melody look-ahead values such as 100 / 200 / 300 / 500 ms
- subjective latency testing with touch interaction

---

## 17. Current architecture implication

The experiments support continuing with Arduino UNO Q 2GB as the primary candidate.

Current preferred conceptual split:

```text
UNO Q Linux / QRB2210
  - BLE MIDI reception
  - melody buffering / look-ahead
  - chord/song state
  - Jazz Engine
  - accompaniment generation

STM32U585
  - deterministic scheduled hardware events
  - accordion solenoid control
  - touch-bar acquisition
  - watchdog and fail-safe
  - optional physical MIDI transmission to MU80
```

The measured Bridge RTT suggests that interactive control is feasible, but production timing should still use an MCU-side scheduled event queue so occasional Linux/Bridge latency spikes do not directly become note timing jitter.

---

## 18. Recommended next milestone

### M3: BLE MIDI -> Linux -> Bridge -> STM32 LED

Goal:

```text
iPhone BLE MIDI
    -> host Linux BLE receiver
    -> decoded Note On/Off
    -> Router Bridge
    -> STM32
    -> built-in LED
```

Suggested behavior:

- any relevant Note On -> LED ON
- matching Note Off / no active notes -> LED OFF

This milestone validates the full control path before connecting the real accordion solenoid hardware.

After M3 passes, proceed to scheduled event queues, accordion MCP23017 output and touch-bar integration.

---

## 19. Retrospective: advertising owner after peer removal

After the known iPhone peer was removed, a fresh bind attempt showed the BLE MIDI
UUID on the adapter but `ActiveInstances: 0`. A complete search of the Git
history, reflog and unreachable objects found no repository-owned
`LEAdvertisingManager1`/`GattManager1` implementation, systemd unit or App Lab
Bluetooth code. The only preserved successful advertising evidence is section 7:
an interactive `bluetoothctl` process registered the advertisement and
`ActiveInstances` became 1.

BlueZ's `bluetoothctl` exports its advertisement at
`/org/bluez/advertising`. BlueZ associates a registered advertisement with that
D-Bus client and removes the instance when the client disconnects. Therefore the
observed zero instance is consistent with the successful interactive
`bluetoothctl` owner no longer running; it is not evidence that the already
proven Bleak receiver is wrong.

The adapter still listing `03b80e5a-ede8-4b33-a751-6ce34ec4c700` indicates that
the local BLE MIDI registration has a lifecycle separate from that missing
advertisement. The repository does not contain enough evidence to name that
GATT application's process. It must be identified on the board from the live
system D-Bus object owners and running system/user services. The UNO Q doctor
now collects those facts automatically. No historic `btmon` capture or complete
advertise-menu command transcript was committed, so neither is reconstructed by
guesswork.
