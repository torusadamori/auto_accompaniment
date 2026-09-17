"""Run with python -m autoaccomp.main."""
import argparse
from contextlib import nullcontext
import time
import mido
from .config import TEMPO, PROGRESSION
from .chord_progression import progression
from .transport import run
from . import comping, walking_bass
from .scheduler import Scheduler
from .midi_io import list_ports, input_port, output_port, forward_pending


def parser():
    result = argparse.ArgumentParser(description="PC AutoAccomp MVP (Ctrl+C to stop)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("ports", help="List MIDI input/output devices")
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
    for name in ("monitor", "thru", "test-tone"):
        command = commands.add_parser(name)
        if name != "test-tone":
            command.add_argument("--input", required=True)
        if name != "monitor":
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
        if args.command == "progression":
            run(progression(args.chords), args.tempo, args.bars,
                lambda bar, chord, next_chord: print(f"Bar {bar + 1}: {chord.symbol}", flush=True))
            return
        if args.command == "play":
            play_accompaniment(args)
            return
        deadline = time.perf_counter() + args.seconds if args.seconds > 0 else float("inf")
        if args.command == "monitor":
            with input_port(args.input) as source:
                print("Listening. Play your keyboard; Ctrl+C stops.", flush=True)
                while time.perf_counter() < deadline:
                    forward_pending(source, monitor=True)
                    time.sleep(0.001)
        else:
            with output_port(args.output) as target:
                target.send(mido.Message("program_change", channel=0, program=0))
                if args.command == "test-tone":
                    target.send(mido.Message("note_on", channel=0, note=60, velocity=70))
                    time.sleep(0.5)
                    target.send(mido.Message("note_off", channel=0, note=60))
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
            for channel in (0, 1):
                target.send(mido.Message("program_change", channel=channel, program=0))
            target.send(mido.Message("program_change", channel=2, program=32))
            scheduler = Scheduler(target)
            previous = None

            def on_bar(bar, chord, next_chord):
                nonlocal previous
                if not args.no_comping:
                    events, previous = comping.generate(chord, bar, previous)
                    scheduler.add(events, bar * 4)
                if not args.no_bass:
                    scheduler.add(walking_bass.generate(chord, next_chord, bar), bar * 4)
                print(f"Bar {bar + 1}: {chord.symbol}", flush=True)

            def service():
                if source is not None:
                    forward_pending(source, target)

            print("Playing in 4/4. Ctrl+C stops.", flush=True)
            run(chords, args.tempo, args.bars, on_bar, service, scheduler.tick)
            print(f"Late attacks skipped: {scheduler.skipped}")


if __name__ == "__main__":
    main()
