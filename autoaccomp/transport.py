"""One monotonic clock for input, harmony and accompaniment."""
import math
import time
from .config import BEATS_PER_BAR


def run(chords, tempo, bars, on_bar, service=lambda: None, on_tick=lambda beat: None):
    if not math.isfinite(tempo) or not 20 <= tempo <= 300:
        raise ValueError("Tempo must be between 20 and 300 BPM.")
    if bars < 0:
        raise ValueError("Bars must be >= 0 (0 means continuous).")
    start = time.perf_counter()
    seconds_per_beat = 60 / tempo
    last_bar = -1
    while True:
        service()
        beat = (time.perf_counter() - start) / seconds_per_beat
        if bars and beat >= bars * BEATS_PER_BAR:
            return
        bar = int(beat // BEATS_PER_BAR)
        if bar != last_bar:
            # After a stall resume at the current bar; never burst through old bars.
            on_bar(bar, chords[bar % len(chords)], chords[(bar + 1) % len(chords)])
            last_bar = bar
        on_tick(beat)
        time.sleep(0.001)
