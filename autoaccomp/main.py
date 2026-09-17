"""Run with python -m autoaccomp.main."""
import argparse
import time
import mido
from .config import TEMPO, PROGRESSION
from .chord_progression import progression
from .transport import run
from .midi_io import list_ports, input_port, output_port, forward_pending


def parser():
    result = argparse.ArgumentParser(description="PC AutoAccomp MVP (Ctrl+C to stop)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("ports", help="List MIDI input/output devices")
    clock = commands.add_parser("progression", help="Run the fixed 4/4 chord clock")
    clock.add_argument("--tempo", type=float, default=TEMPO)
    clock.add_argument("--bars", type=int, default=0)
    clock.add_argument("--chords", nargs="+", default=PROGRESSION)
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


if __name__ == "__main__":
    main()
