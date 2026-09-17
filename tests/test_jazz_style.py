import random
import unittest
from unittest.mock import patch
from autoaccomp.chord_progression import parse_chord
from autoaccomp.jazz_style import PATTERNS, JazzAccompaniment, voicing, bass_line, bass_root
from autoaccomp.jazz_harmony import JazzHarmony
from autoaccomp.progression_estimator import ProgressionEstimator, CandidateScore
from autoaccomp.melody_history import MelodyNote
from autoaccomp.melody_follow import MelodyFollower
import test_melody_cli as cli_tests


class Output:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class JazzTests(unittest.TestCase):
    def test_seed_reproducible_and_pattern_bag_varied(self):
        def render(seed):
            out, logs = Output(), []
            engine = JazzAccompaniment(out, seed=seed, debug_accomp=True, report=logs.append)
            engine.detected = parse_chord("C")
            for i in range(3201):
                engine.tick_accompaniment(i/100)
            engine.scheduler.clear()
            return out.messages, [line for line in logs if line.startswith("Comping pattern:")]
        messages, patterns = render(1)
        self.assertEqual((messages, patterns), render(1))
        self.assertNotEqual(patterns, render(2)[1])
        names = [line.split(": ")[1][0] for line in patterns]
        self.assertEqual(set(names[:4]), set(PATTERNS))
        self.assertTrue(all(a != b for a,b in zip(names, names[1:])))

    def test_voicings_are_rootless_and_smooth_for_every_candidate_pair(self):
        chords = [parse_chord(s) for s in ("C", "Dm", "Em", "F", "G", "Am")]
        for chord in chords:
            initial = voicing(chord)
            self.assertNotIn(chord.root, {n%12 for n in initial})
            for following in chords:
                for low in (False, True):
                    notes = voicing(following, initial, low)
                    self.assertTrue(all(52 <= n <= 72 for n in notes))
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(initial, notes)), 7)
        self.assertEqual({n%12 for n in voicing(parse_chord("C"))}, {4,11,2})
        self.assertEqual({n%12 for n in voicing(parse_chord("G"))}, {11,5,4})
        self.assertGreaterEqual(sum(voicing(chords[0], low_melody=True)), sum(voicing(chords[0])))

    def test_bass_approaches_next_root_with_bounded_leaps(self):
        for first in ("C", "Dm", "Em", "F", "G", "Am"):
            for following in ("C", "Dm", "Em", "F", "G", "Am"):
                chord, nxt = parse_chord(first), parse_chord(following)
                line, target = bass_line(chord, nxt, 40, random.Random(1))
                self.assertEqual(line[0]%12, chord.root)
                self.assertEqual(target%12, nxt.root)
                self.assertIn(abs(line[-1]-target), (1,2))
                self.assertEqual(bass_root(nxt, line[-1]), target)
                self.assertTrue(all(36 <= n <= 50 for n in line))
                self.assertGreaterEqual(len(set(line)), 3)
                self.assertLessEqual(max(abs(a-b) for a,b in zip(line,line[1:])), 7)

    def test_pending_target_guides_fourth_beat_and_actual_next_root(self):
        out = Output()
        engine = JazzAccompaniment(out, report=lambda _: None)
        engine.detected = parse_chord("Dm")
        for beat in (0,1,2):
            engine.tick_accompaniment(beat)
        engine.context((), parse_chord("G"))
        engine.tick_accompaniment(3)
        approach = engine.last_bass
        engine.detected = parse_chord("G")
        engine.context(())
        engine.tick_accompaniment(4)
        self.assertEqual(engine.last_bass%12, 7)
        self.assertIn(abs(engine.last_bass-approach), (1,2))

    def test_dense_melody_reduces_comping_without_affecting_bass(self):
        def render(dense):
            out = Output()
            engine = JazzAccompaniment(out, report=lambda _: None)
            engine.detected = parse_chord("C")
            if dense:
                engine.context(tuple(MelodyNote(55,80,0,0, -3+i*0.4) for i in range(8)))
            for i in range(400):
                engine.tick_accompaniment(i/100)
            return [m for m in out.messages if m.type == "note_on"]
        sparse, dense = render(False), render(True)
        self.assertEqual(sum(m.channel == 1 for m in sparse), 6)
        self.assertEqual(sum(m.channel == 1 for m in dense), 3)
        self.assertEqual(sum(m.channel == 2 for m in dense), 4)

    def test_jazz_debug_is_observation_only_and_releases_notes(self):
        def render(debug):
            out = Output()
            engine = JazzAccompaniment(out, debug_accomp=debug, report=lambda _: None)
            engine.detected = parse_chord("C")
            for i in range(800):
                engine.tick_accompaniment(i/100)
            engine.scheduler.clear()
            active = set()
            for msg in out.messages:
                key = (msg.channel, msg.note)
                if msg.type == "note_on":
                    self.assertNotIn(key, active)
                    active.add(key)
                else:
                    active.remove(key)
            self.assertFalse(active)
            return out.messages
        self.assertEqual(render(False), render(True))

    def test_moderate_changes_wait_for_bar_and_commit(self):
        estimator = JazzHarmony()
        estimator.current = parse_chord("C")
        estimator.last_change = 0
        parts = {c.symbol: CandidateScore(0) for c in estimator.candidates}
        parts["C"], parts["F"] = CandidateScore(2), CandidateScore(2.6)
        note = MelodyNote(65,80,0,0,0)
        with patch.object(estimator, "score_parts", return_value=parts):
            self.assertEqual(estimator.estimate([note], 2)[0].symbol, "C")
            self.assertEqual(estimator.pending.symbol, "F")
            self.assertEqual(estimator.pending_beat, 4)
            self.assertEqual(estimator.estimate([note], 3)[0].symbol, "C")
            self.assertEqual(estimator.estimate([], 4)[0].symbol, "F")
            self.assertEqual(estimator.last_change, 4)

    def test_strong_melody_allows_half_bar_but_not_odd_beat(self):
        for beat, expected in ((1,"C"), (2,"Dm"), (3,"C")):
            estimator = JazzHarmony()
            estimator.current, estimator.last_change = parse_chord("C"), 0
            parts = {c.symbol: CandidateScore(0) for c in estimator.candidates}
            parts["C"], parts["Dm"] = CandidateScore(0.5), CandidateScore(3)
            with patch.object(estimator, "score_parts", return_value=parts):
                chord, _ = estimator.estimate([MelodyNote(62,80,0,0,0)], beat)
                self.assertEqual(chord.symbol, expected)

    def test_jazz_suppresses_frequent_moderate_changes(self):
        counts = []
        for cls in (ProgressionEstimator, JazzHarmony):
            estimator = cls()
            estimator.current, estimator.last_change = parse_chord("C"), 0
            changes = 0
            for beat in range(1,17):
                target = "F" if (beat//2)%2 else "C"
                parts = {c.symbol: CandidateScore(0) for c in estimator.candidates}
                parts["C"], parts["F"] = CandidateScore(2), CandidateScore(2)
                parts[target] = CandidateScore(2.6)
                previous = estimator.current
                with patch.object(estimator, "score_parts", return_value=parts):
                    estimator.estimate([MelodyNote(60,80,0,0,0)], beat)
                changes += estimator.current != previous
            counts.append(changes)
        self.assertLess(counts[1], counts[0])

    def test_cli_basic_matches_default_and_jazz_mute_keeps_actual_logs(self):
        harness = cli_tests.MelodyCliTests()
        flags = ["--progression-aware", "--debug-accomp"]
        _, default, _, _ = harness.exercise(flags)
        _, basic, _, _ = harness.exercise(flags+["--style", "basic", "--seed", "9"])
        self.assertEqual(default.messages, basic.messages)
        _, jazz, _, log = harness.exercise(flags+["--style", "jazz", "--seed", "1", "--mute-melody"])
        self.assertFalse(any(m.type == "note_on" and m.channel == 0 for m in jazz.messages))
        self.assertTrue(any(m.type == "note_on" and m.channel == 1 for m in jazz.messages))
        self.assertTrue(any(m.type == "note_on" and m.channel == 2 for m in jazz.messages))
        self.assertIn("Comping pattern:", log)
        self.assertIn("Comping notes:", log)
        self.assertIn("Bass note:", log)
        self.assertIn("Sent MIDI Note On: Melody=0", log)


if __name__ == "__main__":
    unittest.main()
