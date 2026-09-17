"""Timestamped JSON capture and deterministic replay through the live engine."""
import hashlib
from contextlib import nullcontext
import json
import math
from pathlib import Path
import time
import mido
from .config import MELODY_CHANNEL, COMP_CHANNEL, BASS_CHANNEL
from .midi_io import input_port, output_port
from .melody_follow import MelodyFollower
from .output_diagnostics import AuditedOutput, configure_melody_output

FORMAT = "autoaccomp-melody-v1"
INPUT_TYPES = {"note_on", "note_off", "polytouch", "aftertouch", "pitchwheel", "control_change"}


def validate_tempo(tempo):
    if not math.isfinite(tempo) or not 20 <= tempo <= 300:
        raise ValueError("Tempo must be between 20 and 300 BPM.")


class RecordingClick:
    """Absolute beat deadlines: GM high/low wood blocks on percussion channel 10."""
    def __init__(self, output, origin, seconds_per_beat, count_beats, continuous):
        self.output, self.origin, self.spacing = output, origin, seconds_per_beat
        self.count_beats, self.continuous = count_beats, continuous
        self.next_beat = 0
        self.off = None

    def tick(self, now):
        if self.off is not None and now >= self.off[0]:
            self.output.send(mido.Message("note_off", channel=9, note=self.off[1], velocity=0))
            self.off = None
        if now < self.origin + self.next_beat*self.spacing:
            return
        beat = max(self.next_beat, int((now-self.origin)/self.spacing))
        self.next_beat = beat+1
        # Include the recording downbeat even when only the count-in is requested.
        if not self.continuous and beat > self.count_beats:
            return
        note = 76 if beat % 4 == 0 else 77
        self.output.send(mido.Message("note_on", channel=9, note=note, velocity=110 if beat % 4 == 0 else 85))
        self.off = (now+0.04, note)


def record(args):
    validate_tempo(args.tempo)
    if not math.isfinite(args.seconds) or args.seconds < 0:
        raise ValueError("Seconds must be >= 0 and finite.")
    path = Path(args.output_file)
    if path.suffix.lower() != ".json":
        raise ValueError("Recordings use JSON; specify an output file ending in .json.")
    count_bars = getattr(args, "count_in_bars", 0)
    continuous = getattr(args, "click", False)
    if count_bars < 0:
        raise ValueError("Count-in bars must be >= 0.")
    clicking = count_bars > 0 or continuous
    target = output_port(getattr(args, "output", "Microsoft GS Wavetable Synth 0")) if clicking else nullcontext()
    # Exclusive creation avoids silently overwriting the one performance to compare.
    with input_port(args.input) as source, target as click_output, path.open("x", encoding="utf-8") as destination:
        events = []
        if clicking:
            click_output.send(mido.Message("control_change", channel=9, control=7, value=100))
            click_output.send(mido.Message("control_change", channel=9, control=11, value=127))
        # Establish the grid only after device/file setup. Never move it to the first note.
        origin = time.perf_counter() + (0.25 if clicking else 0)
        start = origin + count_bars*4*60/args.tempo
        metronome = RecordingClick(click_output, origin, 60/args.tempo, count_bars*4, continuous) if clicking else None
        last_status = start
        if clicking:
            print(f"Count-in: {count_bars} bars, 4/4, {args.tempo} BPM. High click = downbeat. "
                  "Recording starts on the downbeat AFTER the count-in.", flush=True)
        print(f"Recording {source.name} -> {path}; tempo={args.tempo}. Ctrl+C saves and stops.", flush=True)
        announced = not clicking
        try:
            while not args.seconds or time.perf_counter() < start+args.seconds:
                now = time.perf_counter()
                if metronome:
                    metronome.tick(now)
                if not announced and now >= start:
                    print("Recording beat 0 / time 0. Play now.", flush=True)
                    announced = True
                for _ in range(256):
                    message = source.poll()
                    if message is None:
                        break
                    received = time.perf_counter()
                    if received >= start and message.type in INPUT_TYPES:
                        events.append({"time_us": round((received-start)*1_000_000),
                                       "bytes": message.bytes()})
                if time.perf_counter()-last_status >= 5:
                    print(f"Recorded events: {len(events)}", flush=True)
                    last_status = time.perf_counter()
                time.sleep(0.001)
        except KeyboardInterrupt:
            pass
        finally:
            duration = max(round((time.perf_counter()-start)*1_000_000),
                           events[-1]["time_us"] if events else 0)
            json.dump({"format": FORMAT, "tempo": args.tempo, "duration_us": duration,
                       "count_in_bars": count_bars, "beat_zero": "count-in-end" if clicking else "record-start",
                       "events": events}, destination, ensure_ascii=False, indent=2, allow_nan=False)
            print(f"Saved {len(events)} events, {duration/1e6:.3f}s: {path}", flush=True)


def load_recording(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Unsupported recording format (expected autoaccomp JSON).")
    tempo, duration, events = data.get("tempo"), data.get("duration_us"), data.get("events")
    if type(tempo) not in (int, float):
        raise ValueError("Invalid recording tempo.")
    validate_tempo(tempo)
    if type(duration) is not int or duration < 0 or not isinstance(events, list):
        raise ValueError("Invalid recording duration/events.")
    previous = -1
    parsed = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid recording event.")
        stamp, raw = event.get("time_us"), event.get("bytes")
        if type(stamp) is not int or not previous <= stamp <= duration or stamp < 0:
            raise ValueError("Recording timestamps must be ordered and within duration.")
        if not isinstance(raw, list) or not raw or any(type(n) is not int for n in raw):
            raise ValueError("Invalid MIDI bytes.")
        message = mido.Message.from_bytes(raw)
        if message.type not in INPUT_TYPES:
            raise ValueError(f"Unsupported recorded message: {message.type}")
        parsed.append((stamp/1e6, message))
        previous = stamp
    return tempo, duration/1e6, parsed


class TimelineOutput:
    def __init__(self):
        self.now = 0.0
        self.events = []

    def send(self, message):
        self.events.append((self.now, message.copy()))


def render(tempo, duration, inputs, style="basic", seed=1, mute_melody=False):
    """Use exact logical times, not OS wake-up times, for all harmony decisions."""
    validate_tempo(tempo)
    output = TimelineOutput()
    # Both styles share progression-aware scoring as their baseline. Only style differs.
    follower = MelodyFollower(output, tempo=tempo, start=0, progression_aware=True,
                              style=style, seed=seed, report=lambda _: None)
    seconds_per_beat = 60/tempo
    end = max(4, math.ceil(duration/seconds_per_beat/4)*4)*seconds_per_beat
    index = boundary = 0
    changes = []
    while True:
        input_time = inputs[index][0] if index < len(inputs) else float("inf")
        queued_time = (follower.scheduler.queue[0][0]*seconds_per_beat
                       if follower.scheduler.queue else float("inf"))
        boundary_time = boundary*seconds_per_beat
        now = min(input_time, queued_time, boundary_time, end)
        output.now = now
        while index < len(inputs) and inputs[index][0] <= now:
            _, message = inputs[index]
            follower.receive(message, now)
            if not mute_melody:
                output.send(message.copy(channel=MELODY_CHANNEL, time=0))
            index += 1
        if now >= end:
            break
        # Queue deadlines are exact beat values; avoid roundoff falling just before a beat.
        beat = boundary if now == boundary_time else (
            follower.scheduler.queue[0][0] if now == queued_time else now/seconds_per_beat)
        previous = follower.engine.current
        follower.tick(beat, now)
        if follower.engine.current != previous:
            changes.append((beat, follower.engine.current.symbol))
        if now == boundary_time:
            boundary += 1
    follower.scheduler.clear()
    return output.events, changes, end


def replay(args):
    tempo, duration, inputs = load_recording(args.input_file)
    if args.tempo is not None:
        # Tempo is the harmony clock; recorded real-time input remains identical.
        tempo = args.tempo
    events, changes, end = render(tempo, duration, inputs, args.style, args.seed, args.mute_melody)
    digest = hashlib.sha256(Path(args.input_file).read_bytes()).hexdigest()[:16]
    print(f"Recording: {digest}; Tempo: {tempo}; Seed: {args.seed}; Style: {args.style}", flush=True)
    print(f"Input events: {len(inputs)}; duration including final bar: {end:.3f}s; "
          f"Melody: {'muted' if args.mute_melody else 'enabled'}", flush=True)
    print("Replay uses progression-aware harmony for both styles. Ctrl+C stops.", flush=True)
    with output_port(args.output) as port:
        output = AuditedOutput(port)
        configure_melody_output(output, False)  # Identical instruments for A/B; no debug voicing substitution.
        # Make comparison levels independent of previous debug sessions.
        for channel in (MELODY_CHANNEL, COMP_CHANNEL, BASS_CHANNEL):
            output.send(mido.Message("control_change", channel=channel, control=7, value=100))
            output.send(mido.Message("control_change", channel=channel, control=11, value=127))
        start = time.perf_counter()
        applied = []
        change_index = 0
        max_late = 0.0
        try:
            for due, message in events:
                while (remaining := start+due-time.perf_counter()) > 0:
                    time.sleep(min(remaining, 0.002))
                max_late = max(max_late, time.perf_counter()-start-due)
                output.send(message)
                while change_index < len(changes) and changes[change_index][0]*60/tempo <= due:
                    applied.append(changes[change_index])
                    change_index += 1
                if args.debug_accomp and message.type == "note_on" and message.velocity:
                    if message.channel == COMP_CHANNEL:
                        print(f"Comping note sent: {message.note} at {due:.3f}s", flush=True)
                    elif message.channel == BASS_CHANNEL:
                        print(f"Bass note sent: {message.note} at {due:.3f}s", flush=True)
            while (remaining := start+end-time.perf_counter()) > 0:
                time.sleep(min(remaining, 0.002))
        finally:
            print(f"Style: {args.style}\nChord changes: {max(0, len(applied)-1)} (initial chord excluded)\n"
                  f"Comping note-ons: {output.attacks[COMP_CHANNEL]}\nBass note-ons: {output.attacks[BASS_CHANNEL]}\n"
                  f"Melody note-ons: {output.attacks[MELODY_CHANNEL]}", flush=True)
            print("Estimated progression: " + " | ".join(f"{beat:g}:{chord}" for beat, chord in applied), flush=True)
            print(f"Maximum MIDI send lateness: {max_late*1000:.1f}ms", flush=True)
