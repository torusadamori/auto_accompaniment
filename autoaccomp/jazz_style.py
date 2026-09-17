"""Seeded comping and voice-led bass; no access to future melody."""
from itertools import product
import random
from .config import COMP_CHANNEL, BASS_CHANNEL
from .events import Note
from .follow import DebugOutput
from .scheduler import Scheduler

# Zero-based beats: A = musical beats 2/4, B = 1.5/3, etc.
PATTERNS = {"A": (1.0, 3.0), "B": (0.5, 2.0), "C": (1.5, 3.0), "D": (0.0, 2.5)}


def voicing(chord, previous=None, low_melody=False):
    if chord.root == 7 and chord.intervals[1] == 4:
        intervals = (4, 10, 9)  # G: B/F/E (dominant seventh + thirteenth)
    elif chord.intervals[1] == 3:
        intervals = (3, 10, 7 if chord.root == 4 else 2)  # Em avoids F# in C major
    else:
        intervals = (4, 11, 2)
    pcs = [(chord.root+i) % 12 for i in intervals]
    choices = [[n for n in range(52, 73) if n % 12 == pc] for pc in pcs]
    candidates = {tuple(sorted(notes)) for notes in product(*choices) if max(notes)-min(notes) <= 16}
    anchor = previous or ((60, 65, 70) if low_melody else (55, 60, 65))
    # Limit individual voice motion whenever such an inversion is available.
    smooth = [notes for notes in candidates if max(abs(a-b) for a,b in zip(notes, anchor)) <= 7]
    candidates = smooth or candidates
    return min(candidates, key=lambda notes: (
        sum(abs(a-b) for a,b in zip(notes, anchor)) + (sum(max(0,60-n) for n in notes)*0.6 if low_melody else 0), notes))


def bass_root(chord, previous=None):
    roots = [n for n in range(36, 51) if n % 12 == chord.root]
    return min(roots, key=lambda n: (abs(n-(previous if previous is not None else 40)), n))


def bass_line(chord, next_chord, previous, rng):
    root = bass_root(chord, previous)
    target = bass_root(next_chord, root)
    seventh = 10 if chord.intervals[1] == 3 or chord.root == 7 else 11
    pitch_classes = set(chord.pitch_classes) | {(chord.root+seventh) % 12}
    tones = [n for n in range(36, 51) if n % 12 in pitch_classes]
    scale = [n for n in range(36, 51) if n % 12 in (0,2,4,5,7,9,11)]
    approaches = [n for n in range(36,51) if abs(n-target) in (1,2)]
    # Prefer chord tones, but allow stepwise scale tones to reduce leaps.
    candidates = []
    for second, third, fourth in product(tones, sorted(set(tones+scale)), approaches):
        line = (root, second, third, fourth)
        leaps = [abs(b-a) for a,b in zip(line, line[1:])]
        cost = sum(leaps) + 3*sum(max(0, jump-5) for jump in leaps)
        cost += 6*sum(a == b for a,b in zip(line, line[1:])) + 2*(4-len(set(line)))
        cost += 0.4*(third not in tones) + 0.2*(abs(fourth-target) == 2)
        candidates.append((cost, line))
    best = min(cost for cost, _ in candidates)
    near = sorted(line for cost, line in candidates if cost <= best+1)
    return rng.choice(near), target


class JazzAccompaniment:
    def __init__(self, output, no_bass=False, no_comping=False, report=print, debug_accomp=False, seed=1):
        self.debug_output = DebugOutput(output, report) if debug_accomp else None
        self.scheduler = Scheduler(self.debug_output or output)
        self.rng = random.Random(seed)
        self.no_bass, self.no_comping = no_bass, no_comping
        self.report = report
        self.detected = self.current = None
        self.previous = None
        self.last_bass = None
        self.last_beat = self.last_bar = -1
        self.pattern = None
        self.order = []
        self.notes = ()
        self.next_chord = None
        self.line = None
        self.target = None
        self.dense = False

    def context(self, notes, next_chord=None):
        self.notes, self.next_chord = notes, next_chord

    def choose_pattern(self):
        if not self.order:
            self.order = list(PATTERNS)
            self.rng.shuffle(self.order)
            if self.order[-1] == self.pattern:
                self.order[0], self.order[-1] = self.order[-1], self.order[0]
        self.pattern = self.order.pop()

    def tick_accompaniment(self, beat):
        boundary, bar, phase = int(beat), int(beat)//4, int(beat)%4
        if boundary != self.last_beat:
            self.last_beat = boundary
            changed = self.detected != self.current
            if changed:
                self.scheduler.clear()
                self.current = self.detected
                self.report(f"Active accompaniment chord: {self.current.symbol}")
            if self.current is not None:
                if bar != self.last_bar:
                    self.last_bar = bar
                    self.choose_pattern()
                    recent = [n for n in self.notes if boundary-4 < n.onset_beat <= boundary]
                    self.dense = len(recent) >= 6
                if changed or phase == 0 or self.line is None:
                    low = bool(self.notes) and sum(n.note for n in self.notes)/len(self.notes) < 60
                    self.previous = voicing(self.current, self.previous, low)
                    self.line, self.target = bass_line(self.current, self.next_chord or self.current, self.last_bass, self.rng)
                    if self.debug_output:
                        self.report(f"Comping pattern: {self.pattern}; density={'thin' if self.dense else 'normal'}; "
                                    f"Voicing (planned): {list(self.previous)}")
                        self.report(f"Bass (planned): {list(self.line)}; Next chord: "
                                    f"{self.next_chord.symbol if self.next_chord else 'unknown (current-root fallback)'}")
                events = []
                hits = PATTERNS[self.pattern]
                if self.dense:
                    hits = hits[:1]
                if not self.no_comping:
                    for hit in hits:
                        if phase <= hit < phase+1:
                            events.extend(Note(hit-phase, 0.48, pitch, 54, COMP_CHANNEL) for pitch in self.previous)
                if not self.no_bass:
                    pitch = self.line[phase]
                    if changed:
                        pitch = bass_root(self.current, self.last_bass)
                    if phase == 3 and self.next_chord is not None:
                        self.target = bass_root(self.next_chord, self.last_bass)
                        approach = [n for n in range(36,51) if abs(n-self.target) in (1,2)]
                        pitch = min(approach, key=lambda n: (abs(n-(self.last_bass or pitch)), abs(n-self.target), n))
                    self.last_bass = pitch
                    events.append(Note(0, 0.92, pitch, 70 if changed or phase == 0 else 64, BASS_CHANNEL))
                self.scheduler.add(events, boundary)
        try:
            self.scheduler.tick(beat)
        finally:
            if self.debug_output:
                self.debug_output.flush(self.current, beat)
