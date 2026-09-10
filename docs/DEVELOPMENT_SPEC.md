# Development Specification

## 1. Scope

本仕様書はAuto Accompanimentの初期実装に必要な責務分離、イベントモデル、時間同期、Jazz Engine、タッチバー、MU80出力、安全設計、検証順序を定義する。

初期版のゴールは以下。

1. BLE MIDIで受信した主旋律を一定時間遅延させて半自動アコーディオンで演奏する
2. 主旋律の未来バッファをLinux側で参照できるようにする
3. タッチバーのtap/slideを低遅延で取得する
4. 主旋律 + タッチ操作 + コード情報からピアノ伴奏を生成する
5. MU80へMIDIを出力してスピーカーから伴奏を鳴らす
6. 主旋律生音と伴奏が音楽的に同期する

初期版ではAI/LLMによる生成、音声合成、画像UIなどは対象外とする。

---

## 2. Target Hardware

### Arduino UNO Q 2GB

MPU/Linux side:

- Qualcomm Dragonwing QRB2210
- Debian Linux
- 2GB RAM
- onboard Bluetooth / Wi-Fi

MCU side:

- STM32U585
- Arduino Core on Zephyr
- deterministic low-level I/O

External:

- Existing semi-automatic accordion solenoid driver
- MCP23017-based I/O as required by current accordion design
- 40 cm touch bar prototype
- Yamaha MU80
- DIN MIDI OUT circuit or validated USB/UART alternative

---

## 3. Responsibility Split

### 3.1 Linux / MPU

Linux is the musical decision layer.

Responsibilities:

- BLE MIDI transport/session management
- Parse NOTE ON/OFF and relevant realtime/control messages
- Monotonic timestamp assignment at input
- Maintain melody look-ahead queue
- Maintain musical clock / tempo / beat position
- Maintain song/chord state
- Analyze recent and future melody
- Receive normalized touch gestures from MCU
- Run Jazz Engine
- Generate accompaniment MIDI events
- Schedule accompaniment events
- Logging / telemetry
- Configuration persistence
- Development/debug API or CLI

Linux must not be the sole owner of safety-critical solenoid OFF timing.

### 3.2 STM32U585 / MCU

MCU is the deterministic execution and hardware-safety layer.

Responsibilities:

- Receive timestamped accordion events from Linux
- Execute solenoid ON/OFF at scheduled time
- Enforce maximum-on-time regardless of Linux state
- Read touch sensor(s)
- Convert raw touch values to normalized touch events
- Send touch events to Linux
- Watchdog/heartbeat supervision
- Panic all-solenoids-off
- Optional physical MIDI DIN transmission to MU80 if this gives better timing consistency

MCU should continue to fail safe even if Linux process crashes.

---

## 4. Timing Model

### 4.1 Monotonic time

All internal scheduling must use a monotonic clock. Wall-clock/UTC is only for log timestamps and never for note scheduling.

Define:

```text
t_rx       = Linux monotonic time when BLE MIDI event is accepted
lookahead  = configured fixed melody delay

t_play = t_rx + lookahead
```

The accordion event corresponding to incoming melody note is scheduled at `t_play`.

### 4.2 Why look-ahead exists

The delay is not CPU processing time. It intentionally creates knowledge of future melody.

At wall time `now`, the engine may know melody events whose physical accordion playback time falls in:

```text
[now, now + lookahead]
```

This allows accompaniment logic to inspect upcoming notes, rests and phrase endings.

### 4.3 Touch time model

Touch gestures originate from a human reacting to the currently audible music.

Therefore:

```text
TouchEvent.timestamp = current audible-time interaction
```

Do not add `lookahead` again to the touch input merely because melody events were delayed.

Instead, when touch arrives at `now`, Jazz Engine may query the future melody queue for events scheduled after `now`.

### 4.4 Quantization

Touch-generated accompaniment may be:

- IMMEDIATE: minimum latency response
- NEXT_16TH: next 1/16 grid
- NEXT_8TH: next 1/8 grid
- NEXT_BEAT: next beat

Default prototype target: `NEXT_16TH` or `NEXT_8TH` depending on gesture.

Suggested mapping:

```text
tap        -> NEXT_8TH or NEXT_BEAT
slow slide -> NEXT_16TH sequence
fast slide -> NEXT_16TH or denser run
```

### 4.5 Tempo changes

Open design question:

- fixed millisecond look-ahead, or
- beat-relative look-ahead

Prototype should start with fixed milliseconds because it is easy to reason about and test.

---

## 5. Event Models

### 5.1 Incoming MIDI Event

```text
MidiInputEvent {
    timestamp_us
    status
    channel
    data1
    data2
}
```

For NOTE events, normalized convenience representation may also be used:

```text
MelodyEvent {
    received_us
    scheduled_play_us
    type: NOTE_ON | NOTE_OFF
    note: 0..127
    velocity: 0..127
    channel
}
```

### 5.2 MCU Scheduled Solenoid Event

```text
SolenoidEvent {
    execute_us
    solenoid_id
    action: ON | OFF
    source_note
    sequence_id
}
```

The MCU must be able to reject stale events and cancel a sequence.

### 5.3 Touch Event

```text
TouchEvent {
    timestamp_us
    active
    position: 0.0..1.0
    direction: -1 | 0 | +1
    speed: 0.0..1.0
    pressure_or_strength: optional
    touched_segments: optional bitset
}
```

### 5.4 Accompaniment MIDI Event

```text
AccompanimentEvent {
    execute_us
    port
    channel
    type
    note
    velocity
    duration_us
    source: TAP | SLIDE | AUTO_FILL | COMPING | OTHER
}
```

---

## 6. Linux <-> MCU Communication

Use UNO Q supported RPC/Bridge mechanism unless benchmarking proves it unsuitable.

Required logical messages:

Linux -> MCU:

```text
HEARTBEAT
SYNC / TIMEBASE
SCHEDULE_SOLENOID
CANCEL_SOLENOID
ALL_OFF
OPTIONAL_SCHEDULE_MIDI_OUT
CONFIG_SAFETY_LIMITS
```

MCU -> Linux:

```text
HEARTBEAT_ACK
TOUCH_EVENT
EXECUTION_REPORT
FAULT
QUEUE_STATUS
```

### Requirements

- No allocation-heavy protocol is needed for note events
- Sequence numbers required
- Queue overflow must be detectable
- Stale commands must not execute
- MCU should report actual execution timestamp for latency/jitter measurement

---

## 7. Accordion Playback Pipeline

```text
BLE MIDI
   |
Linux MIDI receiver
   |
Timestamp + parse
   |
Melody look-ahead queue
   |
Map MIDI note -> accordion actuator(s)
   |
Schedule at t_rx + LOOKAHEAD_MS
   |
RPC
   |
STM32 event queue
   |
Exact-time GPIO / MCP23017 control
   |
Solenoid
   |
Acoustic accordion note
```

The existing M5Stamp implementation should be treated as functional reference behavior when migrating actuator mapping and note logic.

---

## 8. Touch Bar Processing

### 8.1 MVP sensor topology

Start simple.

Candidate MVP:

- 24 segments over ~400 mm
- ~16.7 mm per segment
- conductive filament touch regions
- 2 x MPR121 if 24 electrodes are used

Alternative:

- 32 segments using additional controller(s)

Do not optimize for visually continuous sensing until musical usability has been validated.

### 8.2 Raw processing

MCU should sample raw/filtered touch state and derive:

```text
segment index
normalized position
position delta
direction
speed
touch start
touch end
```

### 8.3 Adjacent simultaneous touch

Sliding fingers will often touch two neighboring electrodes.

Do not treat this as an error.

Possible interpolation:

```text
position = weighted centroid of active adjacent pads
```

If only binary touched/not-touched information is available, use midpoint between adjacent active pads.

### 8.4 Gesture classification

Initial thresholds should be configurable rather than hard-coded.

```text
TAP
HOLD
SLOW_SLIDE_UP
SLOW_SLIDE_DOWN
FAST_SLIDE_UP
FAST_SLIDE_DOWN
```

---

## 9. Jazz Engine Architecture

### 9.1 Design principle

The player provides intent/contour. The engine enforces musical plausibility.

This is closer to assisted improvisation than autonomous composition.

### 9.2 Engine state

```text
JazzState {
    key
    current_chord
    chord_scale
    tempo
    meter
    musical_position
    recent_melody
    future_melody
    recent_accompaniment
    last_piano_note
    last_touch
    style
}
```

### 9.3 Touch position to provisional pitch

Map `position` to a configurable pitch range.

Example:

```text
0.0 -> C3
1.0 -> C6
```

Do not assume one chromatic semitone per pad. Position is an expressive contour first; pitch mapping is separate.

### 9.4 Pitch snapping

Given a target pitch and allowed pitch-class table:

1. find nearest legal note
2. consider previous output note
3. preserve requested movement direction where possible
4. avoid repeated identical note during an active slide
5. enforce pitch range

Pseudo logic:

```text
candidate = nearest_allowed(target, chord_scale)

if sliding_up and candidate <= last_note:
    candidate = next_allowed_above(last_note)

if sliding_down and candidate >= last_note:
    candidate = next_allowed_below(last_note)
```

This intentionally follows the useful part of Loopian's note-translation concept without importing the entire sequencer.

### 9.5 Musical vocabulary by complexity

A `jazz_degree` or style parameter may progressively widen allowed vocabulary.

```text
0-20   triad/chord tones
20-40  + 7th
40-60  + 9th
60-75  + 11th/13th as appropriate
75-90  + diatonic approach/passing notes
90-100 + chromatic approach/enclosure/altered tones where harmonically valid
```

This is a design starting point, not a fixed musical rule.

### 9.6 Main melody awareness

Important features to calculate:

```text
recent_note_density
future_note_density
upcoming_rest_duration
upcoming_long_note_duration
melody_direction
next_phrase_target_note
melody_register
```

Suggested accompaniment rules:

```text
if recent_note_density is high:
    reduce comping density

if upcoming_rest_duration is large:
    increase fill probability

if upcoming_long_note_duration is large:
    permit run/arpeggio

if melody_register is high:
    bias piano lower

if melody_register is low:
    allow piano upper-register response
```

### 9.7 Randomness

Randomness should modify performance, not decide basic harmony.

Good uses:

- small velocity variation
- small timing dispersion
- choose among several equivalent voicings
- select one of several valid fill templates

Bad MVP uses:

- unrestricted random pitch selection
- random chord substitution without context

---

## 10. Loopian Reference Study

Reference repository:

https://github.com/hasebems/Loopian_Rust

Priority files/areas to study:

```text
loopian_app/src/elapse/elapse_flow.rs
loopian_app/src/elapse_loop/note_translation.rs
loopian_app/src/elapse_loop/elapse_loop_phr.rs
loopian_app/src/elapse_loop/floating_tick.rs
loopian_app/src/elapse/elapse_note.rs
```

Concepts of interest:

- position -> provisional pitch
- chord/scale nearest-note correction
- movement-direction-aware arpeggio correction
- duplicate-note avoidance
- beat-aware velocity shaping
- random velocity dispersion
- random timing dispersion

Do not copy unrelated Loopian notation, UI, graphics, full sequencer or live-coding framework unless separately justified.

Any directly reused MIT-licensed source must keep required copyright/license attribution.

---

## 11. MU80 Output

### 11.1 MVP

Use one MU80 piano channel for accompaniment.

Later expand to:

```text
Piano
Bass
Drums
```

### 11.2 Physical output options

Option A: Linux sends MIDI directly via USB/UART adapter.

Pros:

- simplest software routing

Cons:

- Linux scheduling jitter may appear

Option B: Linux generates events, STM32 transmits physical DIN MIDI at scheduled timestamps.

Pros:

- same deterministic timebase can coordinate accordion solenoid and MU80

Cons:

- more firmware work
- Linux<->MCU queue design required

Recommendation: benchmark both. Prefer Option B if timing measurements or listening tests show an advantage.

---

## 12. Safety / Failure Handling

Solenoid safety is mandatory.

### MCU-enforced limits

- MAX_SOLENOID_ON_MS
- no stale scheduled ON event execution
- automatic OFF if matching OFF is not received
- all-off on watchdog timeout
- all-off on communication reset
- all-off on explicit panic input
- queue overflow fault

### Linux failures

If Jazz Engine crashes:

- accordion playback should either continue safely from queued events or stop cleanly
- accompaniment should stop
- MCU watchdog eventually clears any unsafe state

If BLE disconnects:

- do not keep last NOTE ON active
- cancel future melody events as appropriate
- all relevant solenoids off

---

## 13. Logging and Measurement

For every important event, record:

```text
BLE receive timestamp
Linux parse timestamp
Linux scheduled playback timestamp
RPC send timestamp
MCU receive timestamp
MCU actual execute timestamp
MU80 transmit timestamp if MCU-driven
```

This makes latency/jitter measurable rather than subjective.

Metrics:

```text
BLE input jitter
Linux->MCU latency
scheduled execution error
accordion actuator delay
MU80 note timing error
end-to-end audible alignment
```

---

## 14. Configuration

Keep values in a human-readable config file during development.

Candidate fields:

```text
lookahead_ms = 300
quantize_mode = "16th"
min_touch_speed = ...
fast_slide_threshold = ...
accordion_channel = ...
piano_channel = ...
mu80_output_mode = ...
jazz_degree = 60
max_solenoid_on_ms = ...
```

---

## 15. Software Layout Proposal

```text
auto_accompaniment/
├─ README.md
├─ CLAUDE.md
├─ docs/
│  ├─ CONCEPT_REVIEW.md
│  └─ DEVELOPMENT_SPEC.md
├─ linux/
│  ├─ midi_input/
│  ├─ timing/
│  ├─ melody_analysis/
│  ├─ jazz_engine/
│  ├─ mu80/
│  └─ app/
├─ mcu/
│  ├─ scheduler/
│  ├─ accordion/
│  ├─ touchbar/
│  ├─ midi_out/
│  └─ safety/
├─ shared/
│  └─ protocol/
├─ tests/
│  ├─ timing/
│  ├─ jazz_engine/
│  └─ integration/
└─ tools/
   ├─ midi_replay/
   └─ simulation/
```

This layout is conceptual; adapt to UNO Q App Lab / Arduino project constraints after platform validation.

---

## 16. Development Milestones

### M0 - Host simulation

- Rule-based Jazz Engine on PC/Linux
- MIDI-file or scripted melody input
- scripted touch gestures
- output MIDI file
- listen through software synth or MU80

Acceptance:

- tap and slide produce musically plausible piano output
- upward slide normally produces upward contour
- chord changes do not produce obvious wrong notes

### M1 - UNO Q BLE MIDI ingest

- receive BLE MIDI on Linux
- timestamp events
- log stable NOTE ON/OFF stream

Acceptance:

- no stuck notes in long test
- reconnect behavior understood

### M2 - Fixed-delay accordion relay

- send BLE notes through look-ahead queue
- Linux -> STM32 scheduled event transfer
- STM32 actuator scheduler

Acceptance:

- configurable 0/300/500/1000 ms delay
- constant delay with low jitter
- all-off safety proven

### M3 - Touch bar

- connect prototype bar
- derive position/direction/speed
- send TouchEvent to Linux

Acceptance:

- fast/slow slides distinguishable
- left/right direction reliable
- adjacent-pad transitions feel continuous enough for performance

### M4 - Jazz Engine live integration

- consume current/future melody
- consume touch gestures
- generate piano events

Acceptance:

- tap -> comping
- slide -> arpeggio/run
- melody density affects accompaniment density

### M5 - MU80 output

- route generated MIDI to MU80
- choose initial piano patch
- benchmark Linux vs MCU physical MIDI output if necessary

Acceptance:

- accompaniment remains synchronized with acoustic accordion

### M6 - Musical tuning

- compare look-ahead delays
- tune gesture thresholds
- tune style vocabulary
- add humanization

Acceptance:

- player reports that accompaniment feels responsive rather than like backing-track playback

### M7 - Expansion

Optional:

- bass
- drums
- multiple jazz styles
- song/chord maps
- record/replay
- display/UI

---

## 17. MVP Definition

The MVP is deliberately small:

```text
BLE melody
 -> 300 ms delayed accordion

Touch bar
 -> tap/slide gesture

Jazz Engine
 -> C-major / basic ii-V-I-aware pitch snapping
 -> piano-only accompaniment

MU80
 -> audible piano
```

A successful MVP should demonstrate the core experience before adding complexity:

**the performer plays/hears the accordion melody, gestures on the bar, and a musically coherent piano player appears to answer.**
