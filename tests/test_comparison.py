"""Ablation tests: the non-selected musical parts must remain identical."""
import io
from contextlib import redirect_stdout
from itertools import product
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import test_record_replay as recordings
import test_melody_cli as cli
from autoaccomp.comparison import FEATURES, ComparisonAccompaniment
from autoaccomp.follow import Follower
from autoaccomp.chord_progression import progression
from autoaccomp.main import main
from autoaccomp.melody_recording import render, TimelineOutput


def part(result, channel):
    return [(t,m) for t,m in result[0] if m.channel == channel]


class ComparisonTests(unittest.TestCase):
    def render(self, **flags):
        return render(120, 8, recordings.inputs(), seed=1, **flags)

    def test_disabled_features_preserve_both_existing_styles(self):
        for style in ("basic", "jazz"):
            self.assertEqual(render(120,8,recordings.inputs(),style),
                             render(120,8,recordings.inputs(),style, **dict.fromkeys(FEATURES, False)))
        # The modular engine's baseline also agrees with the original Follower.
        results = []
        for engine_type in (Follower, ComparisonAccompaniment):
            output = TimelineOutput()
            engine = engine_type(output)
            chords = progression(["C", "F", "Dm", "G"])
            with redirect_stdout(io.StringIO()):
                for i in range(1600):
                    beat = i/100
                    output.now = beat
                    engine.detected = chords[min(int(beat)//4,3)]
                    engine.tick_accompaniment(beat)
            engine.scheduler.clear()
            results.append(output.events)
        self.assertEqual(*results)

    def test_voicing_only_changes_comping_pitch(self):
        base, changed = self.render(), self.render(jazz_voicing=True)
        self.assertEqual(base[1:], changed[1:])
        self.assertEqual(part(base,0), part(changed,0))
        self.assertEqual(part(base,2), part(changed,2))
        self.assertNotEqual(part(base,1), part(changed,1))

        self.assertEqual([(t,m.copy(note=0)) for t,m in part(base,1)],
                         [(t,m.copy(note=0)) for t,m in part(changed,1)])

    def test_bass_only_changes_bass_pitch(self):
        base, changed = self.render(), self.render(smooth_bass=True)
        self.assertEqual(base[1:], changed[1:])
        self.assertEqual(part(base,0), part(changed,0))
        self.assertEqual(part(base,1), part(changed,1))
        self.assertNotEqual(part(base,2), part(changed,2))
        self.assertEqual([(t,m.copy(note=0)) for t,m in part(base,2)],
                         [(t,m.copy(note=0)) for t,m in part(changed,2)])

    def test_rhythm_does_not_change_harmony_bass_or_input(self):
        base, changed = self.render(), self.render(syncopated_comping=True)
        self.assertEqual(base[1:], changed[1:])
        for channel in (0,2):
            self.assertEqual(part(base,channel), part(changed,channel))
        self.assertNotEqual(part(base,1), part(changed,1))

        def pitches_by_chord(result):
            found = {}
            for timestamp, message in part(result,1):
                if message.type != "note_on":
                    continue
                chord = next(chord for beat,chord in reversed(result[1]) if beat <= timestamp*2)
                found.setdefault(chord,set()).add(message.note)
            return found
        self.assertEqual(pitches_by_chord(base), pitches_by_chord(changed))

    def test_stability_changes_harmony_without_enabling_jazz_parts(self):
        base, stable = self.render(), self.render(harmony_stable=True)
        self.assertEqual(part(base,0), part(stable,0))
        self.assertNotEqual(base[1], stable[1])
        self.assertEqual([b for b,_ in stable[1]], [1,8,12])
        # It keeps the original accompaniment engine, not JazzAccompaniment.
        from autoaccomp.comparison import comparison_follower
        follower = comparison_follower(TimelineOutput(),120,1,{"harmony_stable":True})
        self.assertIs(type(follower.engine), Follower)

    def test_all_combinations_repeat_mute_and_random_stream_independence(self):
        for toggles in product((False,True), repeat=4):
            flags = dict(zip(FEATURES,toggles))
            first = self.render(**flags)
            self.assertEqual(first, self.render(**flags))
            muted = self.render(mute_melody=True, **flags)
            self.assertEqual(muted[1:],first[1:])
            self.assertEqual(muted[0],[(t,m) for t,m in first[0] if m.channel != 0])
            # Toggling bass never perturbs pattern RNG; toggling rhythm never perturbs bass RNG.
            self.assertEqual(part(first,1),part(self.render(**{**flags,"smooth_bass":not flags["smooth_bass"]}),1))
            self.assertEqual(part(first,2),part(self.render(**{**flags,"syncopated_comping":not flags["syncopated_comping"]}),2))

    def test_cli_combines_flags_sends_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"take.json"
            recordings.RecordReplayTests().save(path)
            clock = cli.Clock()
            target = cli.Port(clock)
            log = io.StringIO()
            argv = ["autoaccomp","replay-melody","--input-file",str(path),"--output","Synth","--mute-melody"]
            argv += ["--"+name.replace("_","-") for name in FEATURES]
            with patch("sys.argv",argv), patch("time.perf_counter",clock.counter), \
                 patch("time.sleep",clock.sleep), redirect_stdout(log), \
                 patch("autoaccomp.midi_io.backend",return_value=cli.Backend(cli.Port(clock),target)):
                main()
            for name in FEATURES:
                self.assertIn(name.replace("_","-"), log.getvalue())
            for channel in (1,2):
                self.assertTrue(any(m.type=="note_on" and m.channel==channel for m in target.messages))
            self.assertFalse(any(m.type=="note_on" and m.channel==0 for m in target.messages))
            with self.assertRaises(ValueError):
                render(120,8,recordings.inputs(),"jazz",jazz_voicing=True)
