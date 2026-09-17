"""MIDI hardware boundary; musical generators do not import this module."""
from contextlib import contextmanager
import mido
from .config import MELODY_CHANNEL


def backend():
    return mido.Backend("mido.backends.rtmidi")


def list_ports():
    api = backend()
    for label, names in (("INPUT", api.get_input_names()),
                         ("OUTPUT", api.get_output_names())):
        print(label)
        for index, name in enumerate(names):
            print(f"  {index}: {name}")
        if not names:
            print("  (none)")


def resolve_port(value, names):
    if value in names:
        return value
    if value is not None and value.isdecimal() and int(value) < len(names):
        return names[int(value)]
    raise ValueError(f"Port {value!r} not found. Run 'ports' and specify its exact name or index.")


@contextmanager
def output_port(value):
    api = backend()
    name = resolve_port(value, api.get_output_names())
    with api.open_output(name) as port:
        try:
            yield port
        finally:
            # Release sustain, held notes and sounding voices, including on Ctrl+C.
            port.reset()
            port.panic()


def input_port(value):
    api = backend()
    return api.open_input(resolve_port(value, api.get_input_names()))


def forward_pending(source, target=None, monitor=False, on_message=None):
    # Bound each batch so a busy input cannot starve accompaniment scheduling.
    for _ in range(256):
        message = source.poll()
        if message is None:
            break
        if monitor:
            print(message)
        if on_message is not None:
            on_message(message)
        if target is not None and message.type in {
            "note_on", "note_off", "polytouch", "aftertouch", "pitchwheel", "control_change"
        }:
            # Melody occupies channel 1; channels 2/3 are reserved for accompaniment.
            target.send(message.copy(channel=MELODY_CHANNEL, time=0))
