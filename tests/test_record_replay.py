from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import mido
import test_melody_cli as cli
from autoaccomp.melody_recording import FORMAT, record, replay, render, load_recording
from autoaccomp.main import main


def inputs():
    events = []
    for i, pitch in enumerate([60,60,67,67,69,69,67,65,65,64,64,62,62,60]):
        events.append((i*0.5+0.025, mido.Message("note_on", channel=4, note=pitch, velocity=70+i)))
        events.append((i*0.5+0.45, mido.Message("note_off", channel=4, note=pitch, velocity=12)))
    return events


class RecordReplayTests(unittest.TestCase):
    def save(self, path, events=None, duration=8_000_000):
        path.write_text(json.dumps({"format": FORMAT, "tempo": 120, "duration_us": duration,
                                   "events": [{"time_us": round(t*1e6), "bytes": msg.bytes()}
                                              for t,msg in (events if events is not None else inputs())]}), encoding="utf-8")

    def test_record_preserves_timing_bytes_and_refuses_overwrite(self):
        clock = cli.Clock()
        source = cli.Port(clock)
        source.name = "Keyboard"
        source.events = [(0.01,mido.Message("note_on", channel=2,note=48,velocity=93)),
                         (0.02,mido.Message("note_on", channel=2,note=48,velocity=0))]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"melody.json"
            args = SimpleNamespace(input="Keyboard",output_file=str(path),tempo=120,seconds=0.03)
            with patch("autoaccomp.melody_recording.input_port",return_value=source), \
                 patch("time.perf_counter",clock.counter),patch("time.sleep",clock.sleep),redirect_stdout(io.StringIO()):
                record(args)
                before = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    record(args)
                self.assertEqual(path.read_bytes(),before)
            tempo,duration,events = load_recording(path)
            self.assertEqual(tempo,120)
            self.assertAlmostEqual(duration,0.03)
            self.assertAlmostEqual(events[0][0],0.01)
            self.assertAlmostEqual(events[1][0],0.02)
            self.assertEqual(events[0][1].bytes(),[0x92,48,93])
            self.assertEqual(events[1][1].velocity,0)

    def test_ctrl_c_saves_recording(self):
        clock = cli.Clock()
        source = cli.Port(clock)
        source.name = "Keyboard"
        messages = iter([mido.Message("note_on",note=60),KeyboardInterrupt()])
        def poll():
            result = next(messages)
            if isinstance(result,BaseException):
                raise result
            return result
        source.poll = poll
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"take.json"
            with patch("autoaccomp.melody_recording.input_port",return_value=source), \
                 patch("time.perf_counter",clock.counter),redirect_stdout(io.StringIO()):
                record(SimpleNamespace(input="Keyboard",output_file=str(path),tempo=120,seconds=0))
            self.assertEqual(len(load_recording(path)[2]),1)

    def test_same_input_and_velocity_across_styles_and_seed_repeatability(self):
        original = inputs()
        expected = [(t,m.copy(channel=0)) for t,m in original]
        for style in ("basic","jazz"):
            first = render(120,8,original,style,1)
            self.assertEqual(first,render(120,8,original,style,1))
            melody=[(t,m) for t,m in first[0] if m.channel==0]
            self.assertEqual(melody,expected)
            self.assertTrue(any(m.channel==1 and m.type=="note_on" for _,m in first[0]))
            self.assertTrue(any(m.channel==2 and m.type=="note_on" for _,m in first[0]))
            muted=render(120,8,original,style,1,True)
            self.assertEqual(muted[1:],first[1:])
            self.assertEqual(muted[0],[(t,m) for t,m in first[0] if m.channel!=0])

    def test_tempo_and_float_boundaries_release_all_notes(self):
        for tempo in (73,120,137):
            for style in ("basic","jazz"):
                events,_,end=render(tempo,7.03,inputs(),style)
                active=set()
                for timestamp,msg in events:
                    self.assertLessEqual(timestamp,end)
                    if msg.channel==0:
                        continue
                    key=(msg.channel,msg.note)
                    if msg.type=="note_on":
                        self.assertNotIn(key,active)
                        active.add(key)
                    else:
                        active.remove(key)
                self.assertFalse(active)

    def test_replay_real_send_boundary_and_summary_independent_of_sleep_jitter(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"take.json"
            self.save(path)
            results=[]
            for oversleep in (0,0.007):
                clock=cli.Clock()
                out=cli.Port(clock)
                backend=cli.Backend(cli.Port(clock),out)
                log=io.StringIO()
                def sleep(seconds):
                    clock.now+=seconds+oversleep
                with patch("autoaccomp.midi_io.backend",return_value=backend), \
                     patch("time.perf_counter",clock.counter),patch("time.sleep",sleep),redirect_stdout(log), \
                     patch("sys.argv", ["autoaccomp", "replay-melody", "--input-file", str(path),
                                        "--output", "Synth", "--style", "jazz", "--seed", "1",
                                        "--mute-melody", "--debug-accomp"]):
                    main()
                self.assertTrue(out.reset_called and out.panic_called)
                attacks=[m for m in out.messages if m.type=="note_on"]
                self.assertFalse(any(m.channel==0 for m in attacks))
                self.assertIn(f"Comping note-ons: {sum(m.channel==1 for m in attacks)}",log.getvalue())
                self.assertIn(f"Bass note-ons: {sum(m.channel==2 for m in attacks)}",log.getvalue())
                self.assertIn("Chord changes:",log.getvalue())
                self.assertEqual(log.getvalue().count("Comping note sent:"), sum(m.channel==1 for m in attacks))
                self.assertEqual(log.getvalue().count("Bass note sent:"), sum(m.channel==2 for m in attacks))
                results.append(out.messages)
            self.assertEqual(results[0],results[1])

    def test_malformed_or_unsorted_recording_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"take.json"
            self.save(path,[(1,mido.Message("note_on")),(0,mido.Message("note_off"))])
            with self.assertRaises(ValueError):
                load_recording(path)
            path.write_text('{"format":"wrong"}',encoding="utf-8")
            with self.assertRaises(ValueError):
                load_recording(path)


if __name__=="__main__":
    unittest.main()
