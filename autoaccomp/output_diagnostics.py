"""Count successful sends at the final output boundary, including MIDI thru."""
from collections import Counter
import mido
from .config import MELODY_CHANNEL, COMP_CHANNEL, BASS_CHANNEL


class AuditedOutput:
    def __init__(self, output):
        self.output = output
        self.attacks = Counter()

    def send(self, message):
        self.output.send(message)
        if message.type == "note_on" and message.velocity > 0:
            self.attacks[message.channel] += 1

    def summary(self):
        return (f"Sent MIDI Note On: Melody={self.attacks[MELODY_CHANNEL]} "
                f"Comping={self.attacks[COMP_CHANNEL]} Bass={self.attacks[BASS_CHANNEL]}")


def configure_melody_output(output, debug=False):
    output.send(mido.Message("program_change", channel=MELODY_CHANNEL, program=0))
    output.send(mido.Message("program_change", channel=COMP_CHANNEL, program=4 if debug else 0))
    output.send(mido.Message("program_change", channel=BASS_CHANNEL, program=32))
    if debug:
        # Set explicit GM volume/expression so previous synth settings do not mute parts.
        for channel, volume in ((MELODY_CHANNEL, 88), (COMP_CHANNEL, 108), (BASS_CHANNEL, 112)):
            output.send(mido.Message("control_change", channel=channel, control=7, value=volume))
            output.send(mido.Message("control_change", channel=channel, control=11, value=127))
