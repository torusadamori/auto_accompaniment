"""Bounded local melody search with weak, seeded variation.

The cursor is the next expected source position, independent of output pitch.
Selection is speculative; only commit() records a successfully emitted attack.
"""
from collections import deque
from copy import copy
from dataclasses import dataclass
import math
import random

from .midi_melody import SourceNote


def gesture_strength(interval):
    size = abs(interval)
    return "SMALL" if size <= 2 else "MEDIUM" if size <= 5 else "LARGE"


def phrase_speed(elapsed):
    return "FAST" if elapsed is not None and elapsed < 0.18 else (
        "SLOW" if elapsed is None or elapsed >= 0.4 else "NORMAL")


@dataclass(frozen=True)
class WindowEvent:
    position: int  # absolute, including loops
    source: SourceNote  # retains contour, duration, velocity, track/channel
    bar: int
    beat: float
    role: str  # role of source pitch in the CURRENT harmony


@dataclass(frozen=True)
class FlowCandidate:
    position: int
    note: int
    cost: float  # lower is better
    role: str
    contour_match: bool
    repetition_cost: float
    motif_bonus: float
    phrase_cost: float = 0


@dataclass(frozen=True)
class FlowSelection:
    cursor: int
    selected: FlowCandidate
    context: object
    pitch: object
    window: tuple
    candidates: tuple
    finalists: tuple
    strength: str
    speed: str

    @property
    def next_position(self):
        return self.selected.position + 1


class FlowSelector:
    def __init__(self, window=3, strength=0.5, seed=None):
        if not isinstance(window, int) or not 1 <= window <= 8:
            raise ValueError("Flow window must be an integer between 1 and 8.")
        if not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError("Flow strength must be finite and between 0 and 1.")
        self.window_size, self.amount, self.seed = window, strength, seed
        self.reset()

    def reset(self):
        self.rng = random.Random(self.seed)
        self.history = deque(maxlen=8)  # (output note, input direction, source position)
        self.previous_phrase = ()
        self.holds = 0
        self.last_selection = None

    def window(self, song, cursor, allowed, tones=None):
        tones = allowed[:4] if tones is None else tones
        result = []
        for position in range(max(0, cursor - self.window_size), cursor + self.window_size + 1):
            source = song.melody[position % len(song.melody)]
            context = song.context_for(position)
            role = "PRIMARY" if source.note % 12 in tones else (
                "SECONDARY" if source.note % 12 in allowed else "OUTSIDE")
            result.append(WindowEvent(position, source, context.bar, context.beat, role))
        return tuple(result)

    def advance_limit(self, strength, speed):
        limit = 1 + int(self.amount >= 0.25)
        if self.amount >= 0.75 or (self.amount >= 0.5 and strength == "LARGE"):
            limit = 3
        if speed == "FAST":
            limit = 1
        return min(limit, self.window_size + 1)

    def repetition_cost(self, note, direction, history=None):
        history = list(self.history if history is None else history)
        if direction == "SAME" or not history:
            return 0.0  # Intentional repetition is a human instruction.
        notes = [entry[0] for entry in history]
        cost = 0.0
        if len(notes) >= 3 and notes[-3:] == [note] * 3:
            cost += 2
        if len(notes) >= 5 and notes[-5:] == [notes[-1], note, notes[-1], note, notes[-1]]:
            directions = [entry[1] for entry in history[-5:]] + [direction]
            intentional = all(a != b and a in ("UP", "DOWN") and b in ("UP", "DOWN")
                              for a, b in zip(directions, directions[1:]))
            cost += 0.8 if intentional else 3
        if len(notes) >= 4:
            steps = [b - a for a, b in zip(notes[-4:], notes[-3:])]
            if steps[0] and len(set(steps + [note - notes[-1]])) == 1:
                cost += 0.6
        if len(history) < len(self.previous_phrase) and self.previous_phrase[len(history)] == note:
            cost += 0.4
        return self.amount * cost

    def motif_bonus(self, song, position, direction, history):
        if len(history) < 2 or position < 2:
            return 0.0
        original = tuple(song.melody[i % len(song.melody)].contour for i in range(position - 2, position + 1))
        human = tuple(h[1] for h in history[-2:]) + (direction,)
        return 0.6 * self.amount if original == human else 0.0

    def select(self, pitch, song, gesture, cursor, palettes, range_restart=False):
        context = song.context_for(cursor)
        allowed, tones = context.allowed, context.chord_tones
        window = self.window(song, cursor, allowed, tones)
        source = song.melody[cursor % len(song.melody)]
        probe = copy(pitch)
        base_note = probe.choose(gesture, context, range_restart=range_restart, material=source)
        strength, speed = gesture_strength(gesture.interval), phrase_speed(gesture.elapsed)
        # Boundaries use the proven Phase 2 rebase. In particular, a RANGE_LIMIT
        # preview consumes neither RNG nor memory while the real gap is pending.
        if pitch.previous is None or probe.boundary_reason:
            role = "PRIMARY" if base_note % 12 in tones else "SECONDARY"
            candidate = FlowCandidate(cursor, base_note, 0, role, False, 0, 0)
            return FlowSelection(cursor, candidate, context, probe, window, (candidate,),
                                 (candidate,), strength, speed)

        history = list(self.history)
        limit = self.advance_limit(strength, speed)
        positions = []
        for index in range(cursor, cursor + limit):
            other = song.context_for(index)
            # Never skip across a bar or chord boundary; the first note in the
            # new context must be heard before exploring farther forward.
            if other.bar != context.bar or other.chord != context.chord:
                break
            positions.append(index)
        if self.amount >= 0.25 and cursor and not self.holds and source.phrase_break != "STRONG":
            other = song.context_for(cursor - 1)
            if other.bar == context.bar and other.chord == context.chord:
                positions.append(cursor - 1)  # one hold allowed, never reverse the cursor
        previous = pitch.previous
        leap = 7 if strength == "LARGE" and self.amount >= 0.5 else 5
        notes = [n for n in range(pitch.low, pitch.high + 1) if n % 12 in allowed]
        if gesture.direction in ("UP", "DOWN"):
            sign = 1 if gesture.direction == "UP" else -1
            notes = [n for n in notes if 0 < (n - previous) * sign <= leap]
            if not notes:
                notes = [base_note]  # Sparse palettes: Phase 2's nearest valid directional note.
        else:
            notes = [base_note]  # SAME retains Phase 2's hold/chord-change semantics.
        strong = (any(0 <= context.beat - beat < 0.25 for beat in context.accents)
                  or pitch.change_notes > 0 or pitch.last_chord != context.chord)
        primary = [n for n in notes if n % 12 in tones]
        if strong and primary and pitch.tone_priority == "chord":
            notes = primary
        penalty = 4 if strong else 2.5 if speed == "SLOW" else 1 if speed == "FAST" else 1.5
        candidates = []
        for position in positions:
            material = song.melody[position % len(song.melody)]
            crossed = [song.melody[i % len(song.melody)].phrase_break for i in range(cursor + 1, position + 1)]
            if crossed.count("STRONG") > 1:
                continue
            phrase_cost = 3 * crossed.count("STRONG") + 0.75 * crossed.count("WEAK")
            advance = position + 1 - cursor
            proximity = 0.6 * abs(position - cursor) + (0.7 if advance == 0 else 0)
            if speed == "FAST" and advance != 1:
                proximity += 1
            if strength == "SMALL" and advance > 1:
                proximity += 0.5
            elif strength == "LARGE" and advance == 2:
                proximity -= 0.5 * self.amount
            elif advance == 3:
                proximity += 0.5  # +3 remains rare even for large gestures
            match = material.contour == gesture.direction
            contour = (-0.7 if match else 0.35) * self.amount
            motif = self.motif_bonus(song, position, gesture.direction, history)
            importance = (min(material.duration, 2) * 0.15 + material.velocity / 127 * 0.15
                          if speed == "SLOW" else 0)
            for note in notes:
                if gesture.direction in ("UP", "DOWN"):
                    step = min(leap, max(1, abs(material.interval)))
                    target = previous + (step if gesture.direction == "UP" else -step)
                else:
                    target = previous
                role = "PRIMARY" if note % 12 in tones else "SECONDARY"
                chord_cost = penalty if role == "SECONDARY" and pitch.tone_priority == "chord" else 0
                identity_bonus = (0.25 if strong and note % 12 in tuple(tones[i] for i in (1, 3) if i < len(tones)) else 0)
                distance = abs(note - previous)
                repeat = self.repetition_cost(note, gesture.direction, history)
                cost = (0.75 * abs(note - target)
                        + 0.25 * min((note - material.note) % 12, (material.note - note) % 12)
                        + (0.3 if strength == "SMALL" else 0.15) * distance
                        + proximity + contour + chord_cost + repeat + phrase_cost - motif - importance - identity_bonus)
                candidates.append(FlowCandidate(position, note, cost, role, match, repeat, motif, phrase_cost))
        candidates.sort(key=lambda c: (c.cost, abs(c.position - cursor), c.position, c.note))
        # At most three genuinely close choices. Bad candidates never enter the lottery.
        finalists = tuple(c for c in candidates[:3] if c.cost <= candidates[0].cost + 1.5 * self.amount)
        weights = [math.exp(-(c.cost - finalists[0].cost) / (0.2 + 0.6 * self.amount)) for c in finalists]
        selected = self.rng.choices(finalists, weights=weights, k=1)[0]
        probe.previous = selected.note
        probe.selection_reason = (f"FLOW {gesture.direction} + {selected.role} + melody-near; "
                                  f"contour agreement={selected.contour_match}; "
                                  f"motif bonus={selected.motif_bonus:.2f}; repetition cost={selected.repetition_cost:.2f}; "
                                  f"phrase cost={selected.phrase_cost:.2f}; "
                                  f"advance={selected.position + 1 - cursor}; weighted top-{len(finalists)}")
        return FlowSelection(cursor, selected, context, probe, window, tuple(candidates), finalists, strength, speed)

    def commit(self, selection, direction):
        if selection.pitch.boundary_reason:
            self.previous_phrase = tuple(n for n, _, _ in self.history)
            self.history.clear()
        self.holds = self.holds + 1 if selection.next_position == selection.cursor else 0
        self.history.append((selection.selected.note, direction, selection.selected.position))
        self.last_selection = selection

    def debug(self, selection, song, note_name):
        window = "\n".join(f"  idx{e.position % len(song.melody)} {note_name(e.source.note)} {e.role} "
                           f"{e.source.contour} bar={e.bar + 1} beat={e.beat + 1:g} "
                           f"duration={e.source.duration:g} velocity={e.source.velocity} phrase={e.source.phrase_break}" for e in selection.window)
        candidates = "\n".join(f"  idx{c.position % len(song.melody)} {note_name(c.note)} {c.role} "
                               f"cost={c.cost:.3f}" for c in selection.candidates[:6])
        selected = selection.selected
        return (f"FLOW cursor: {selection.cursor}; window=+/-{self.window_size}; amount={self.amount:g}; "
                f"seed={self.seed}\nWindow:\n{window}\n"
                f"Gesture strength: {selection.strength}; Phrase speed: {selection.speed}\n"
                f"Candidates (lower cost is better; best six):\n{candidates}\n"
                f"Selected: idx{selected.position % len(song.melody)} {note_name(selected.note)}; "
                f"next cursor={selection.next_position}\n")
