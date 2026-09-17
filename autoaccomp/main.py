"""Run with python -m autoaccomp.main."""
import argparse
from contextlib import nullcontext
import time
import mido
from .config import TEMPO, PROGRESSION, MELODY_CHANNEL, COMP_CHANNEL, BASS_CHANNEL, BEATS_PER_BAR
from .chord_progression import progression
from .transport import run
from . import comping, walking_bass
from .scheduler import Scheduler
from .midi_io import list_ports, input_port, output_port, forward_pending
from .follow import Follower, run_follow
from .melody_follow import MelodyFollower
from .output_diagnostics import AuditedOutput, configure_melody_output
from .input_diagnostics import monitor, raw_monitor
from .melody_recording import record, replay
from .loopian import DEFAULT_CHORDS, run_loopian


def parser():
    result = argparse.ArgumentParser(description="PC AutoAccomp MVP (Ctrl+C to stop)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("ports", help="List MIDI input/output devices")
    loopian = commands.add_parser("loopian", help="Shape keyboard gestures using an authored C-major song")
    loopian.add_argument("--input", required=True)
    loopian.add_argument("--output", required=True)
    loopian.add_argument("--tempo", type=float, default=120)
    loopian.add_argument("--bars", type=int, default=0)
    loopian.add_argument("--chords", nargs="+", choices=("Cmaj7", "Dm7", "G7"), default=DEFAULT_CHORDS)
    loopian.add_argument("--note-min", type=int, default=55)
    loopian.add_argument("--note-max", type=int, default=84)
    loopian.add_argument("--grid", type=int, choices=(0, 8, 16), default=16, help="0 disables timing correction")
    loopian.add_argument("--timing-strength", type=float, default=0.5)
    loopian.add_argument("--timing-window-ms", type=float, default=30)
    loopian.add_argument("--debug-loopian", action="store_true")
    recording = commands.add_parser("record-melody", help="Record timestamped MIDI input to JSON; Ctrl+C saves")
    recording.add_argument("--input", required=True)
    recording.add_argument("--output-file", required=True)
    recording.add_argument("--tempo", type=float, default=TEMPO)
    recording.add_argument("--seconds", type=float, default=0)
    recording.add_argument("--count-in-bars", type=int, default=0, help="4/4 count-in before recording beat zero")
    recording.add_argument("--click", action="store_true", help="Continue the metronome while recording")
    recording.add_argument("--output", default="Microsoft GS Wavetable Synth 0", help="Metronome MIDI output")
    playback = commands.add_parser("replay-melody", help="Replay the same recorded input for basic/jazz comparison")
    playback.add_argument("--input-file", required=True)
    playback.add_argument("--output", required=True)
    playback.add_argument("--style", choices=("basic", "jazz"), default="basic")
    playback.add_argument("--seed", type=int, default=1)
    playback.add_argument("--tempo", type=float, help="Override harmony tempo; recorded timing stays unchanged")
    playback.add_argument("--mute-melody", action="store_true")
    playback.add_argument("--debug-accomp", action="store_true")
    for option in ("harmony-stable", "jazz-voicing", "smooth-bass", "syncopated-comping"):
        playback.add_argument("--"+option, action="store_true", help="Independent addition to --style basic")
    clock = commands.add_parser("progression", help="Run the fixed 4/4 chord clock")
    clock.add_argument("--tempo", type=float, default=TEMPO)
    clock.add_argument("--bars", type=int, default=0)
    clock.add_argument("--chords", nargs="+", default=PROGRESSION)
    play = commands.add_parser("play", help="Play accompaniment, optionally with keyboard thru")
    play.add_argument("--input", help="Optional keyboard input name or index")
    play.add_argument("--output", required=True)
    play.add_argument("--tempo", type=float, default=TEMPO)
    play.add_argument("--bars", type=int, default=0)
    play.add_argument("--chords", nargs="+", default=PROGRESSION)
    play.add_argument("--no-bass", action="store_true", help="Listen to comping only")
    play.add_argument("--no-comping", action="store_true", help="Listen to bass only")
    follow = commands.add_parser("follow", help="Recognize held chords and follow on the next beat")
    follow.add_argument("--input", required=True)
    follow.add_argument("--output", required=True)
    follow.add_argument("--tempo", type=float, default=TEMPO)
    follow.add_argument("--bars", type=int, default=0)
    follow.add_argument("--no-bass", action="store_true")
    follow.add_argument("--no-comping", action="store_true")
    follow.add_argument("--debug-accomp", action="store_true",
                        help="Log sent accompaniment notes and emphasize chord changes")
    melody = commands.add_parser("melody-follow", help="Estimate C-major harmony from a single-note melody")
    melody.add_argument("--input", required=True)
    melody.add_argument("--output", required=True)
    melody.add_argument("--tempo", type=float, default=TEMPO)
    melody.add_argument("--bars", type=int, default=0)
    melody.add_argument("--key", choices=("C",), default="C")
    melody.add_argument("--no-bass", action="store_true")
    melody.add_argument("--no-comping", action="store_true")
    melody.add_argument("--debug-harmony", action="store_true")
    melody.add_argument("--debug-accomp", action="store_true")
    melody.add_argument("--mute-melody", action="store_true",
                        help="Analyze input normally but output only comping and bass (no MIDI thru)")
    melody.add_argument("--style", choices=("basic", "jazz"), default="basic")
    melody.add_argument("--seed", type=int, default=1)
    melody.add_argument("--progression-aware", action="store_true",
                        help="Prefer stable progressions and bar boundaries; freeze estimation during rests")
    for name in ("monitor", "raw-monitor", "thru", "test-tone"):
        command = commands.add_parser(name)
        if name != "test-tone":
            command.add_argument("--input", required=True)
        if name not in ("monitor", "raw-monitor"):
            command.add_argument("--output", required=True)
        command.add_argument("--seconds", type=float, default=0,
                             help="Stop automatically; 0 means until Ctrl+C")
    return result


def main():
    args = parser().parse_args()
    try:
        if args.command == "ports":
            list_ports()
            return
        if args.command == "loopian":
            run_loopian(args)
            return
        if args.command == "progression":
            run(progression(args.chords), args.tempo, args.bars,
                lambda bar, chord, next_chord: print(f"Bar {bar + 1}: {chord.symbol}", flush=True))
            return
        if args.command == "play":
            play_accompaniment(args)
            return
        if args.command in ("record-melody", "replay-melody"):
            (record if args.command == "record-melody" else replay)(args)
            return
        if args.command == "follow":
            follow_accompaniment(args)
            return
        if args.command == "melody-follow":
            melody_accompaniment(args)
            return
        if args.command in ("monitor", "raw-monitor"):
            diagnostic = raw_monitor if args.command == "raw-monitor" else monitor
            diagnostic(args.input, args.seconds)
            return
        deadline = time.perf_counter() + args.seconds if args.seconds > 0 else float("inf")
        if args.command in ("thru", "test-tone"):
            with output_port(args.output) as target:
                target.send(mido.Message("program_change", channel=MELODY_CHANNEL, program=0))
                if args.command == "test-tone":
                    target.send(mido.Message("note_on", channel=MELODY_CHANNEL, note=60, velocity=70))
                    time.sleep(0.5)
                    target.send(mido.Message("note_off", channel=MELODY_CHANNEL, note=60))
                else:
                    with input_port(args.input) as source:
                        print("MIDI thru ready. Play your keyboard; Ctrl+C stops.", flush=True)
                        while time.perf_counter() < deadline:
                            forward_pending(source, target)
                            time.sleep(0.001)
    except KeyboardInterrupt:
        print("Stopped; notes released.")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)


def play_accompaniment(args):
    chords = progression(args.chords)
    with output_port(args.output) as target:
        with input_port(args.input) if args.input is not None else nullcontext() as source:
            for channel in (MELODY_CHANNEL, COMP_CHANNEL):
                target.send(mido.Message("program_change", channel=channel, program=0))
            target.send(mido.Message("program_change", channel=BASS_CHANNEL, program=32))
            scheduler = Scheduler(target)
            previous = None

            def on_bar(bar, chord, next_chord):
                nonlocal previous
                if not args.no_comping:
                    events, previous = comping.generate(chord, bar, previous)
                    scheduler.add(events, bar * BEATS_PER_BAR)
                if not args.no_bass:
                    scheduler.add(walking_bass.generate(chord, next_chord, bar), bar * BEATS_PER_BAR)
                print(f"Bar {bar + 1}: {chord.symbol}", flush=True)

            def service():
                if source is not None:
                    forward_pending(source, target)

            print("Playing in 4/4. Ctrl+C stops.", flush=True)
            run(chords, args.tempo, args.bars, on_bar, service, scheduler.tick)
            print(f"Late attacks skipped: {scheduler.skipped}")


def follow_accompaniment(args):
    with output_port(args.output) as target:
        with input_port(args.input) as source:
            for channel in (MELODY_CHANNEL, COMP_CHANNEL):
                target.send(mido.Message("program_change", channel=channel, program=0))
            target.send(mido.Message("program_change", channel=BASS_CHANNEL, program=32))
            follower = Follower(target, args.no_bass, args.no_comping,
                                report=lambda line: print(line, flush=True), debug_accomp=args.debug_accomp)
            print("Follow mode: play a chord. Changes apply on the next beat; Ctrl+C stops.", flush=True)
            run_follow(follower, args.tempo, args.bars,
                       lambda: forward_pending(source, target, on_message=follower.receive))
            print(f"Late attacks skipped: {follower.scheduler.skipped}")


def melody_accompaniment(args):
    with output_port(args.output) as port:
        with input_port(args.input) as source:
            target = AuditedOutput(port) if args.debug_accomp else port
            configure_melody_output(target, args.debug_accomp)
            follower = MelodyFollower(target, args.tempo, args.key, args.no_bass, args.no_comping,
                                      report=lambda line: print(line, flush=True),
                                      debug_harmony=args.debug_harmony, debug_accomp=args.debug_accomp,
                                      progression_aware=args.progression_aware, style=args.style, seed=args.seed)
            print(f"Melody follow: C major, style={args.style}, seed={args.seed}. Ctrl+C stops.", flush=True)
            if args.mute_melody:
                print("Melody thru: MUTED (input still used for harmony; only accompaniment is sent).", flush=True)
            if args.debug_accomp:
                print(f"MIDI OUT: {args.output}", flush=True)
                print(f"Melody: ch{MELODY_CHANNEL+1} Piano; "
                      f"Comping: ch{COMP_CHANNEL+1} Electric Piano; "
                      f"Bass: ch{BASS_CHANNEL+1} Acoustic Bass (low register).", flush=True)
                print(f"Comping: {'muted' if args.no_comping else 'enabled'}; "
                      f"Bass: {'muted' if args.no_bass else 'enabled'}.", flush=True)
            elif args.debug_harmony:
                print("Harmony logging only. Add --debug-accomp to see successful MIDI sends.", flush=True)
            try:
                run_follow(follower, args.tempo, args.bars,
                           lambda: forward_pending(source, None if args.mute_melody else target,
                                                   on_message=follower.receive), start=follower.start)
            finally:
                print(f"Late attacks skipped: {follower.scheduler.skipped}")
                if args.debug_accomp:
                    print(target.summary(), flush=True)


if __name__ == "__main__":
    main()
