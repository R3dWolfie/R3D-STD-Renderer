"""osu!(lazer) Wind Up / Wind Down (ModTimeRamp): a clock rate that RAMPS
linearly over the map (WU 1.0->1.5, WD 1.0->0.75 by default), unlike DT/HT's
constant SpeedChange. Read the ramp from the .osr ScoreInfo blob, drive the
record clock + audio off the time-varying rate, and keep every non-ramp render
byte-identical.

Semantics cited from ppy/osu (master):
  * osu.Game/Rulesets/Mods/ModTimeRamp.cs — ApplyToBeatmap sets
    beginRampTime = firstObject.StartTime and finalRateTime = beginRampTime +
    FINAL_RATE_PROGRESS(0.75) * (lastObjectEnd - beginRampTime); ApplyToRate(t)
    = InitialRate + (FinalRate-InitialRate) * clamp((t-begin)/max(1,final-
    begin), 0, 1), Math.Round(.,2). The rate is a function of the GAMEPLAY
    clock time, so dg/dw = rate(g) -> the wall<->map transform is exponential
    in the ramp window (timewarp.TimeWarp).
  * ModWindUp.cs   — "WU": InitialRate 1.0 [0.5,1.99], FinalRate 1.5
    [0.51,2.0], AdjustPitch = BindableBool(true).
  * ModWindDown.cs — "WD": InitialRate 1.0 [0.51,2.0], FinalRate 0.75
    [0.5,1.99], AdjustPitch = BindableBool(true).
  * settings keys: snake_case initial_rate / final_rate / adjust_pitch.
"""
from __future__ import annotations

import json
import lzma
import math
import struct
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from osrparse import GameMode, Key, Mod, Replay
from osrparse.replay import ReplayEventOsu

from osu_std_renderer.record.audio import (CHANNELS, SAMPLE_RATE,
                                           warp_music_pcm)
from osu_std_renderer.record.hitsounds import OneShot, mix_hitsounds
from osu_std_renderer.render.scene import ScenePlayer
from osu_std_renderer.replay import parse_replay
from osu_std_renderer.replay.lazer_mods import (RATE_RAMP_DEFAULTS,
                                                RATE_RAMP_RANGE, RateRamp,
                                                rate_ramp_from_mods,
                                                read_rate_ramp)
from osu_std_renderer.timewarp import (FINAL_RATE_PROGRESS, TimeWarp,
                                       build_time_warp)

LAZER_GV = 30_000_016


# --- synthetic .osr (mirrors tests/test_rate_mods.py) ----------------------------

def _write_osr(dirpath: Path, name: str, mods_json, *,
               bitmask: Mod = Mod.NoMod, blob: bool = True) -> Path:
    ev = [ReplayEventOsu(0, 5.0, 5.0, Key(0)),
          ReplayEventOsu(1000, 256.0, 192.0, Key.K1),
          ReplayEventOsu(-12345, 0.0, 0.0, Key(0))]
    r = Replay(GameMode.STD, LAZER_GV, "abc", "tester", "h",
               1, 0, 0, 0, 0, 0, 12345, 1, False, bitmask, None,
               datetime(2026, 1, 1), ev, 0, None)
    base = r.pack()
    if blob:
        raw = json.dumps({"mods": mods_json, "rank": "S",
                          "statistics": {}}).encode("utf-8")
        comp = lzma.compress(raw, format=lzma.FORMAT_ALONE)
        base = base + struct.pack("<i", len(comp)) + comp
    p = dirpath / name
    p.write_bytes(base)
    return p


def _close(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) <= eps


# --- 1. reading the ramp from the ScoreInfo blob ---------------------------------

def test_ramp_defaults_wu_wd():
    wu = rate_ramp_from_mods([{"acronym": "WU"}])
    assert wu == RateRamp("WU", 1.0, 1.5, True)          # adjust_pitch default True
    wd = rate_ramp_from_mods([{"acronym": "WD", "settings": {}}])
    assert wd == RateRamp("WD", 1.0, 0.75, True)
    # tables agree with lazer's SettingSource defaults / ranges
    assert RATE_RAMP_DEFAULTS == {"WU": (1.0, 1.5), "WD": (1.0, 0.75)}
    assert RATE_RAMP_RANGE["WU"] == ((0.5, 1.99), (0.51, 2.0))
    assert RATE_RAMP_RANGE["WD"] == ((0.51, 2.0), (0.5, 1.99))


def test_ramp_custom_rates_and_pitch():
    r = rate_ramp_from_mods([{"acronym": "WU", "settings": {
        "initial_rate": 1.2, "final_rate": 1.8, "adjust_pitch": False}}])
    assert r == RateRamp("WU", 1.2, 1.8, False)
    r2 = rate_ramp_from_mods([{"acronym": "WD", "settings": {
        "initial_rate": 1.5, "final_rate": 0.6}}])
    assert r2 == RateRamp("WD", 1.5, 0.6, True)           # pitch absent -> True


def test_ramp_rates_clamped_to_range():
    # WU initial in [0.5,1.99], final in [0.51,2.0]
    r = rate_ramp_from_mods([{"acronym": "WU", "settings": {
        "initial_rate": 0.1, "final_rate": 9.0}}])
    assert r.initial == 0.5 and r.final == 2.0
    # WD initial in [0.51,2.0], final in [0.5,1.99]
    r2 = rate_ramp_from_mods([{"acronym": "WD", "settings": {
        "initial_rate": 5.0, "final_rate": 0.01}}])
    assert r2.initial == 2.0 and r2.final == 0.5


def test_ramp_rejects_boolean_rate():
    # a stray JSON bool in a numeric slot -> the mod default, not 1.0/0.0
    r = rate_ramp_from_mods([{"acronym": "WU", "settings": {
        "initial_rate": True, "final_rate": True}}])
    assert r.initial == 1.0 and r.final == 1.5
    # a non-bool adjust_pitch falls back to the default True
    r2 = rate_ramp_from_mods([{"acronym": "WU", "settings": {
        "adjust_pitch": "yes"}}])
    assert r2.adjust_pitch is True


def test_no_ramp_returns_none():
    assert rate_ramp_from_mods([{"acronym": "DT"}, {"acronym": "HD"}]) is None
    assert rate_ramp_from_mods([]) is None


def test_read_ramp_from_osr_and_stable_none():
    d = Path(tempfile.mkdtemp(prefix="stdwuosr_"))
    wu = _write_osr(d, "wu.osr", [{"acronym": "HD"},
                                  {"acronym": "WU", "settings": {
                                      "final_rate": 1.7}}])
    stable = _write_osr(d, "s.osr", None, blob=False)
    assert read_rate_ramp(wu) == RateRamp("WU", 1.0, 1.7, True)
    assert read_rate_ramp(stable) is None


# --- 2. parse_replay surfaces ramp_* on ReplayMeta -------------------------------

def test_parse_replay_surfaces_ramp():
    d = Path(tempfile.mkdtemp(prefix="stdwumeta_"))
    p = _write_osr(d, "wu.osr", [{"acronym": "WU", "settings": {
        "initial_rate": 1.1, "final_rate": 1.9, "adjust_pitch": False}}])
    _, meta = parse_replay(p)
    assert meta.has_rate_ramp is True
    assert meta.ramp_acronym == "WU"
    assert meta.ramp_initial == 1.1 and meta.ramp_final == 1.9
    assert meta.ramp_pitch is False
    assert meta.rate_ramp == (1.1, 1.9, False)
    # a ramp never co-occurs with a constant rate override
    assert meta.rate_override is None


def test_parse_replay_no_ramp_leaves_fields_none():
    d = Path(tempfile.mkdtemp(prefix="stdwunone_"))
    p = _write_osr(d, "dt.osr", [{"acronym": "DT"}], bitmask=Mod.DoubleTime)
    _, meta = parse_replay(p)
    assert meta.has_rate_ramp is False
    assert meta.ramp_initial is None and meta.rate_ramp is None


# --- 3. TimeWarp: rate_at (linear interp + clamp) --------------------------------

def _warp(a=1.0, b=1.5, g0=1000.0, last_end=5000.0):
    return build_time_warp(a, b, g0, last_end)


def test_finalRateTime_is_75pct_of_map():
    w = _warp(1.0, 1.5, g0=1000.0, last_end=5000.0)
    # g1 = g0 + 0.75*(last_end-g0) = 1000 + 0.75*4000 = 4000
    assert _close(w._gm, 1000.0 + FINAL_RATE_PROGRESS * 4000.0)
    assert _close(w._gm, 4000.0)


def test_rate_endpoints_and_midpoint():
    w = _warp(1.0, 1.5, g0=1000.0, last_end=5000.0)   # g1 = 4000
    assert _close(w.rate_at(1000.0), 1.0)             # initial at g0
    assert _close(w.rate_at(4000.0), 1.5)             # final at g1 (75%)
    assert _close(w.rate_at(2500.0), 1.25)            # midpoint of ramp
    # clamped flat outside [g0, g1]
    assert _close(w.rate_at(0.0), 1.0)                # before first object
    assert _close(w.rate_at(500.0), 1.0)
    assert _close(w.rate_at(4500.0), 1.5)             # last 25% pinned at final
    assert _close(w.rate_at(9999.0), 1.5)


def test_rate_linear_quarter_points():
    w = _warp(1.0, 2.0, g0=0.0, last_end=4000.0)      # g1 = 3000
    # linear across [0,3000]: rate = 1 + (g/3000)
    for g, r in [(750.0, 1.25), (1500.0, 1.5), (2250.0, 1.75)]:
        assert _close(w.rate_at(g), r)


def test_wind_down_rate_decreases():
    w = _warp(1.0, 0.75, g0=1000.0, last_end=5000.0)  # g1 = 4000
    assert _close(w.rate_at(1000.0), 1.0)
    assert _close(w.rate_at(4000.0), 0.75)
    assert _close(w.rate_at(2500.0), 0.875)
    assert w.rate_at(3000.0) < w.rate_at(2000.0)      # monotone decreasing


# --- 4. TimeWarp: the wall<->map transform (integral) ----------------------------

def test_to_wall_to_map_are_inverses():
    for a, b in [(1.0, 1.5), (1.0, 0.75), (0.8, 2.0), (1.0, 1.0)]:
        w = _warp(a, b, g0=1000.0, last_end=9000.0)
        for g in [-500.0, 0.0, 1000.0, 2500.0, 5000.0, 7000.0, 12000.0]:
            assert _close(w.to_map(w.to_wall(g)), g, eps=1e-4)
        for wall in [-200.0, 0.0, 500.0, 2000.0, 6000.0]:
            assert _close(w.to_wall(w.to_map(wall)), wall, eps=1e-4)


def test_to_wall_monotonic_increasing():
    w = _warp(1.0, 1.5, g0=1000.0, last_end=9000.0)
    xs = [-1000, 0, 1000, 2000, 4000, 7000, 9000, 12000]
    walls = [w.to_wall(x) for x in xs]
    assert all(walls[i] < walls[i + 1] for i in range(len(walls) - 1))


def test_transform_matches_clock_ode():
    """The closed-form to_map MUST equal the fixed-timestep clock the renderer
    runs (ScenePlayer: t += dt * rate_at(t)). Forward-Euler-integrate the clock
    at a fine step and compare to the closed form at several wall checkpoints."""
    w = _warp(1.0, 1.5, g0=1000.0, last_end=9000.0)    # g1 = 7000
    # start the clock at map time g0 (wall 0) and integrate forward
    dt = 0.05
    t = w.g0
    wall = 0.0
    checkpoints = {500.0, 2000.0, 4000.0, 6000.0, 8000.0}
    seen = {}
    steps = int(9000.0 / dt)
    for _ in range(steps):
        t += dt * w.rate_at(t)
        wall += dt
        for c in list(checkpoints):
            if wall >= c:
                seen[c] = t
                checkpoints.discard(c)
    for c, t_sim in seen.items():
        # closed form maps wall time c (relative to g0) to the same map time
        assert _close(w.to_map(c), t_sim, eps=2.0)     # <2 ms over 8 s


def test_flat_ramp_reduces_to_linear():
    w = _warp(1.25, 1.25, g0=1000.0, last_end=9000.0)  # initial == final
    # rate constant 1.25 everywhere -> to_wall is a pure divide
    assert _close(w.rate_at(3000.0), 1.25)
    assert _close(w.to_wall(1000.0 + 1250.0), 1000.0)  # 1250 map ms -> 1000 wall
    assert _close(w.map_span(2000.0, 1000.0), 1250.0)  # 1000 wall ms -> 1250 map


def test_map_span_uses_local_rate_at_boundaries():
    w = _warp(1.0, 1.5, g0=1000.0, last_end=9000.0)    # g1 = 7000
    # after g1 the rate is pinned at final=1.5: a 1000 ms wall window spans
    # 1500 map ms
    assert _close(w.map_span(8000.0, 1000.0), 1500.0, eps=1e-3)
    # before g0 the rate is initial=1.0: 1000 ms wall -> 1000 map ms
    assert _close(w.map_span(0.0, 1000.0), 1000.0, eps=1e-3)


def test_degenerate_short_window_no_crash():
    # last_end == g0 -> denom clamped to 1 ms (lazer Math.Max(1, ...))
    w = build_time_warp(1.0, 1.5, 1000.0, 1000.0)
    assert _close(w.rate_at(500.0), 1.0)
    assert w.rate_at(2000.0) == 1.5
    assert _close(w.to_map(w.to_wall(3000.0)), 3000.0, eps=1e-3)


# --- 5. rate_bands (audio partition) ---------------------------------------------

def test_rate_bands_cover_and_are_contiguous():
    w = _warp(1.0, 1.5, g0=1000.0, last_end=9000.0)    # g1 = 7000
    bands = w.rate_bands(0.0, 12000.0, quant=0.01)
    assert bands[0][0] == 0.0 and bands[-1][1] == 12000.0
    for i in range(len(bands) - 1):
        assert _close(bands[i][1], bands[i + 1][0])    # contiguous
    # ramp region split ~ |1.5-1.0|/0.01 = 50 bands, plus 2 flat regions
    assert 48 <= len(bands) <= 54
    # band-average rate increases monotonically across the map
    rates = [w.rate_at((m0 + m1) / 2) for (m0, m1) in bands]
    assert all(rates[i] <= rates[i + 1] + 1e-9 for i in range(len(rates) - 1))


def test_rate_bands_flat_when_no_ramp():
    w = _warp(1.0, 1.0, g0=1000.0, last_end=9000.0)
    bands = w.rate_bands(0.0, 12000.0)
    # no ramp -> only the natural cut points, not 50 bands
    assert len(bands) <= 3


# --- 6. warp_music_pcm ------------------------------------------------------------

def _sine(dur_ms: float, freq: float = 440.0) -> np.ndarray:
    n = int(dur_ms / 1000.0 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    s = (0.2 * np.sin(2 * math.pi * freq * t)).astype(np.float32)
    return np.stack([s, s], axis=1)


def test_warp_music_length_matches_integral_pitch():
    w = _warp(1.0, 2.0, g0=500.0, last_end=8000.0)     # g1 = 6125
    pcm = _sine(8000.0)
    out = warp_music_pcm(pcm, w, adjust_pitch=True)     # numpy resample path
    m_hi = len(pcm) / SAMPLE_RATE * 1000.0
    expected_ms = w.to_wall(m_hi) - w.to_wall(0.0)
    expected_n = int(round(expected_ms / 1000.0 * SAMPLE_RATE))
    # WU speeds the song up overall -> shorter than the source
    assert out.shape[1] == CHANNELS
    assert len(out) < len(pcm)
    assert abs(len(out) - expected_n) <= 4               # sample-accurate join


def test_warp_music_flat_rate_identity_length():
    w = _warp(1.0, 1.0, g0=500.0, last_end=8000.0)       # rate 1.0 everywhere
    pcm = _sine(4000.0)
    out = warp_music_pcm(pcm, w, adjust_pitch=True)
    assert abs(len(out) - len(pcm)) <= 4                 # unchanged length


def test_warp_music_wind_down_lengthens():
    w = _warp(1.0, 0.75, g0=500.0, last_end=8000.0)
    pcm = _sine(8000.0)
    out = warp_music_pcm(pcm, w, adjust_pitch=True)
    assert len(out) > len(pcm)                            # slowed down -> longer


# --- 7. hitsound placement follows the warp --------------------------------------

class _ImpulseBank:
    """A stand-in SampleBank: every get() returns a single-frame impulse."""
    def get(self, set_id, sound, index):
        return np.ones((1, CHANNELS), dtype=np.float32), "synth"

    def source_counts(self):
        return {"beatmap": 0, "skin": 0, "synth": 0}


def test_mix_hitsounds_places_oneshot_at_warped_wall_time():
    from osu_std_renderer.record.audio import AudioMixer
    w = _warp(1.0, 1.5, g0=1000.0, last_end=9000.0)
    render_start = 0.0

    def m2w(m):
        return w.to_wall(m) - w.to_wall(render_start)

    mixer = AudioMixer(12000.0)
    hit_map_ms = 8000.0                                   # late map -> faster
    shots = [OneShot(set_id=1, sound="hitnormal", index=0,
                     time_ms=hit_map_ms, volume=1.0)]
    mix_hitsounds(mixer, _ImpulseBank(), shots, [], to_wall=m2w)
    # the impulse must land at sample floor(m2w(hit)/1000*SR), NOT hit/1000*SR
    want_wall_ms = m2w(hit_map_ms)
    idx = int(want_wall_ms / 1000.0 * SAMPLE_RATE)
    assert mixer.buf[idx, 0] != 0.0
    # and NOT at the naive un-warped map position (they must differ under WU)
    naive = int(hit_map_ms / 1000.0 * SAMPLE_RATE)
    assert naive != idx


# --- 8. ScenePlayer clock: ramp follows rate, non-ramp byte-identical ------------

class _StubScene:
    def frame_rgb(self, t):
        return t


def test_sceneplayer_constant_rate_byte_identical():
    # rate_fn=None MUST advance exactly delta*speed (unchanged legacy line)
    a = ScenePlayer(_StubScene(), end_ms=1e9, speed=1.5, start_ms=100.0)
    b = ScenePlayer(_StubScene(), end_ms=1e9, speed=1.5, start_ms=100.0)
    seq_a, seq_b = [], []
    for _ in range(1000):
        a.update(1.0)
        seq_a.append(a.t)
    for _ in range(1000):
        b.update(1.0)
        seq_b.append(b.t)
    assert seq_a == seq_b
    assert seq_a[-1] == 100.0 + 1000 * 1.5                # exact multiply


def test_sceneplayer_ramp_advances_by_instantaneous_rate():
    w = _warp(1.0, 1.5, g0=1000.0, last_end=9000.0)      # g1 = 7000
    p = ScenePlayer(_StubScene(), end_ms=1e9, start_ms=1000.0,
                    rate_fn=w.rate_at)
    # a tick early in the map (rate ~1.0) advances ~1 ms; a tick late (rate
    # 1.5, past g1) advances ~1.5 ms -> late-map objects render faster (WU)
    p.update(0.001)
    early = p.t - 1000.0
    p.t = 8000.0                                          # jump past g1
    p.update(0.001)
    late = p.t - 8000.0
    assert late > early * 1.4                             # ~1.5x vs ~1.0x
    assert _close(late, 0.001 * 1.5, eps=1e-9)


# --- 9. reconcile is unaffected by the ramp (judgments live in MAP time) ---------

def test_reconcile_exact_under_ramp():
    """The ruleset sim runs in MAP time (frame times aren't ramp-compressed),
    so a WU replay reconciles to its .osr counts exactly like nomod."""
    from osu_std_renderer.beatmap import load_full
    from osu_std_renderer.ruleset import StdRuleset
    hdr = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 0

[Metadata]
Title:WindTest
Artist:Unit
Creator:R3D
Version:Ramp

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,1,60,1,0

[HitObjects]
256,192,1000,1,0,0:0:0:0:
"""
    d = Path(tempfile.mkdtemp(prefix="stdwurec_"))
    (d / "m.osu").write_text(hdr, encoding="utf-8")
    beatmap = load_full(d / "m.osu", mods=0)
    osr = _write_osr(d, "wu.osr", [{"acronym": "WU"}])
    frames, meta = parse_replay(osr)
    assert meta.has_rate_ramp
    # the sim reconciles to the (synthetic) .osr counts without raising —
    # the ramp NEVER touches the map-time judgment path
    sim = StdRuleset(beatmap, frames, meta, reconcile=True).run()
    assert sim is not None
