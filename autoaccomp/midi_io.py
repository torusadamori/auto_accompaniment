"""MIDI hardware boundary; musical generators do not import this module."""
from contextlib import contextmanager
import time
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


WINMM_UNPREPARE_ERROR = (
    "MidiInWinMM::openPort: error closing Windows MM MIDI input port (midiInUnprepareHeader)."
)


def close_winmm_input(port):
    """Shutdown adapter for the pinned Mido 1.3.3 / python-rtmidi 1.5.8 backend.

    Do not use is_port_open() to prove cleanup: python-rtmidi clears its Python
    port number BEFORE native closePort(), including on a failing native close.
    """
    if port.closed:
        return
    rt = port._rt
    if rt.is_deleted:
        port.closed = True
        return
    # Mido's callback=None setter re-registers its queue callback. Bypass that
    # setter at shutdown; no consumer or scheduler runs while this function runs.
    rt.ignore_types(sysex=True, timing=True, active_sense=True)
    rt.cancel_callback()
    for _ in range(4096):
        if port.poll() is None:
            break
    time.sleep(0.05)  # Allow in-flight callbacks to finish; never in the play loop.
    try:
        rt.close_port()
    except (OSError, RuntimeError) as error:
        # Only proven completed disposal is harmless. The WinMM error text alone
        # can also mean a buffer still belongs to the driver, which is NOT safe.
        if str(error) != WINMM_UNPREPARE_ERROR or rt.is_deleted is not True:
            raise
    if not rt.is_deleted:
        rt.delete()
    port.closed = True


@contextmanager
def input_port(value):
    api = backend()
    port = api.open_input(resolve_port(value, api.get_input_names()))
    from mido.backends.rtmidi import Input
    if isinstance(port, Input) and port.api == "WINDOWS_MM":
        try:
            yield port
        finally:
            close_winmm_input(port)
    else:
        with port as source:
            yield source


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
