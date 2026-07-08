"""Nightcore beat overlay (record/hitsounds.nightcore_beats — the mania
_layer_nightcore mirror, signature-aware). Pure CPU."""
from __future__ import annotations

from osu_std_renderer.beatmap.beatmap import Beatmap
from osu_std_renderer.record.hitsounds import nightcore_beats


def _timings(points):
    """points: [(time, beat_length, signature)] red lines only."""
    bm = Beatmap()
    for t, bl, sig in points:
        bm.parse_point(f"{t},{bl},{sig},2,1,60,1,0")
    bm.finalize_points()
    return bm.timings


def test_beats_every_beat_finish_on_downbeats():
    tm = _timings([(1000.0, 500.0, 4)])          # 120 BPM, 4/4 from 1s
    beats = nightcore_beats(tm, 0.0, 5000.0)
    times = [t for t, _ in beats]
    assert times == [1000.0, 1500.0, 2000.0, 2500.0, 3000.0, 3500.0,
                     4000.0, 4500.0]
    downs = [t for t, d in beats if d]
    assert downs == [1000.0, 3000.0]             # every 4th beat


def test_signature_drives_the_measure():
    tm = _timings([(0.0, 500.0, 3)])             # 3/4
    beats = nightcore_beats(tm, 0.0, 4000.0)
    downs = [t for t, d in beats if d]
    assert downs == [0.0, 1500.0, 3000.0]        # every 3rd beat


def test_red_line_change_resets_the_measure():
    tm = _timings([(0.0, 500.0, 4), (1750.0, 250.0, 4)])   # BPM jump
    beats = nightcore_beats(tm, 0.0, 3000.0)
    times = [t for t, _ in beats]
    # segment 1: 0, 500, 1000, 1500 (stops at the next red line)
    # segment 2: 1750 + k*250
    assert times[:4] == [0.0, 500.0, 1000.0, 1500.0]
    assert times[4:8] == [1750.0, 2000.0, 2250.0, 2500.0]
    # the new segment restarts its bar count → 1750 is a downbeat
    assert (1750.0, True) in beats


def test_window_clip_and_bpm_sanity_cap():
    tm = _timings([(0.0, 500.0, 4)])
    beats = nightcore_beats(tm, 1000.0, 2100.0)   # [t0, t1) window
    assert [t for t, _ in beats] == [1000.0, 1500.0, 2000.0]
    # a >1000 BPM red line is capped at 60 ms steps, not thousands of hits
    crazy = _timings([(0.0, 1.0, 4)])
    n = len(nightcore_beats(crazy, 0.0, 600.0))
    assert n == 10                                # 600 ms / 60 ms cap
