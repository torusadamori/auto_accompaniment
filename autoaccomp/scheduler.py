"""Convert absolute beat events into MIDI messages on the common transport."""
import heapq
import itertools
import mido


class Scheduler:
    def __init__(self, output):
        self.output = output
        self.queue = []
        self.counter = itertools.count()
        self.active = set()
        self.skipped = 0

    def add(self, notes, offset):
        for note in notes:
            start = offset + note.beat
            end = start + note.duration
            for beat, priority, velocity in ((start, 1, note.velocity), (end, 0, 0)):
                heapq.heappush(self.queue, (beat, priority, next(self.counter),
                                           note.channel, note.pitch, velocity))

    def tick(self, beat):
        while self.queue and self.queue[0][0] <= beat:
            due, priority, _, channel, pitch, velocity = heapq.heappop(self.queue)
            key = (channel, pitch)
            if priority:
                # >1/8 beat late: drop stale attacks instead of making a burst.
                if beat - due > 0.125:
                    self.skipped += 1
                    continue
                self.active.add(key)
                self.output.send(mido.Message("note_on", channel=channel, note=pitch, velocity=velocity))
            elif key in self.active:
                self.active.remove(key)
                self.output.send(mido.Message("note_off", channel=channel, note=pitch, velocity=0))
