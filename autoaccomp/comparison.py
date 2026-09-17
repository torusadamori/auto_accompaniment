"""Independent additions to Basic for recorded-input experiments.

Keep Basic's chord-relative timing, dynamics and note lengths unless rhythm is
explicitly selected. Separate random streams prevent cross-feature coupling.
"""
from dataclasses import replace
import random
from . import comping, walking_bass
from .events import Note
from .config import COMP_CHANNEL
from .follow import Follower
from .jazz_harmony import JazzHarmony
from .jazz_style import PATTERNS, voicing, bass_line
from .melody_follow import MelodyFollower

FEATURES = ("harmony_stable", "jazz_voicing", "smooth_bass", "syncopated_comping")


class ComparisonAccompaniment(Follower):
    def __init__(self, output, seed=1, jazz_voicing=False, smooth_bass=False, syncopated_comping=False):
        super().__init__(output, report=lambda _: None)
        self.jazz_voicing = jazz_voicing
        self.smooth_bass = smooth_bass
        self.syncopated_comping = syncopated_comping
        self.pattern_rng = random.Random(seed)
        self.bass_rng = random.Random(seed)
        self.order = []
        self.pattern = None
        self.last_bar = -1
        self.line = None
        self.last_bass = None

    def tick_accompaniment(self, beat):
        boundary = int(beat)
        if boundary != self.last_beat:
            self.last_beat = boundary
            changed = self.detected != self.current
            if changed:
                self.scheduler.clear()
                self.current = self.detected
                self.chord_start = boundary
            if self.current is not None:
                age = boundary-self.chord_start
                phase = age % 4
                events = []
                # Basic pitches are maintained even when a new rhythm delays the
                # actual attack. Rhythm must not change the voicing history.
                voices = comping.voicing(self.current, self.previous)
                if self.jazz_voicing:
                    voices = voicing(self.current, self.previous)
                basic_hits = comping.PATTERNS[(age//4) % len(comping.PATTERNS)]
                if any(phase <= hit < phase+1 for hit, _, _ in basic_hits):
                    self.previous = voices
                if self.syncopated_comping:
                    bar = boundary//4
                    if bar != self.last_bar:
                        self.last_bar = bar
                        if not self.order:
                            self.order = list(PATTERNS)
                            self.pattern_rng.shuffle(self.order)
                            if self.order[-1] == self.pattern:
                                self.order[0], self.order[-1] = self.order[-1], self.order[0]
                        self.pattern = self.order.pop()
                    rhythm = [(hit, basic_hits[i][1], basic_hits[i][2])
                              for i, hit in enumerate(PATTERNS[self.pattern])]
                    rhythm_phase = boundary % 4
                else:
                    rhythm, rhythm_phase = basic_hits, phase
                for hit, duration, velocity in rhythm:
                    if rhythm_phase <= hit < rhythm_phase+1:
                        events.extend(Note(hit-rhythm_phase, duration, pitch, velocity, COMP_CHANNEL)
                                      for pitch in voices)
                bass = walking_bass.generate(self.current, self.current)[phase]
                if self.smooth_bass:
                    if changed or phase == 0 or self.line is None:
                        self.line, _ = bass_line(self.current, self.current, self.last_bass, self.bass_rng)
                    bass = replace(bass, pitch=self.line[phase])
                    self.last_bass = bass.pitch
                events.append(replace(bass, beat=0))
                self.scheduler.add(events, boundary)
        self.scheduler.tick(beat)


def comparison_follower(output, tempo, seed, features):
    follower = MelodyFollower(output, tempo=tempo, start=0, progression_aware=True, report=lambda _: None)
    if features.get("harmony_stable"):
        follower.estimator = JazzHarmony()
    if any(features.get(name) for name in FEATURES[1:]):
        follower.engine = ComparisonAccompaniment(output, seed, **{name: features.get(name, False) for name in FEATURES[1:]})
        follower.scheduler = follower.engine.scheduler
    return follower
