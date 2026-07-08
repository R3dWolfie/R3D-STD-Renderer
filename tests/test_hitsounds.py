"""Hitsound phase tests — §3.4 sample resolution chain (skin / beatmap
custom-index / synth), bit→sample + layered semantics, volume floor,
tracking-loop windows + timing-point splits, offline-mix determinism.
Judged hit times come from the REAL StdRuleset run over a synthesized
perfect replay (a test fixture, like test_ruleset's frame builders).
Sample-file tests decode via ffmpeg (present on the render boxes)."""
from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

import numpy as np

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.beatmap.objects import Slider, Spinner
from osu_std_renderer.beatmap.objects.timing import TimingPoint
from osu_std_renderer.record.audio import SAMPLE_RATE, AudioMixer
from osu_std_renderer.record.hitsounds import (
    Loop, OneShot, SampleBank, collect_hitsound_events, mix_hitsounds,
    resolve_volume, sounds_for_bits, synth_sample, synth_style_for,
)
from osu_std_renderer.replay.replay import KEY_K1, KEY_K2, StdFrame
from osu_std_renderer.ruleset import StdRuleset
from osu_std_renderer.skin.skin import Skin

MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
StackLeniency: 0.7
Mode: 0

[Metadata]
Title:HitsoundTest
Artist:Unit
Creator:R3D
Version:Sound

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:7
ApproachRate:9
SliderMultiplier:1.0
SliderTickRate:1

[TimingPoints]
{timing}

[HitObjects]
"""

DEFAULT_TIMING = "0,500,4,2,1,60,1,0"    # 120 BPM, soft set, idx 1, vol 60


def _map(objects: str, timing: str = DEFAULT_TIMING) -> object:
    d = Path(tempfile.mkdtemp(prefix="stdhitsound_"))
    p = d / "test.osu"
    p.write_text(MAP_HEADER.format(timing=timing) + objects,
                 encoding="utf-8")
    return load_full(p)


def _perfect_replay_frames(bm) -> list[StdFrame]:
    """Test fixture: a replay that hits everything dead-on — press exactly
    at object time on the object, ride the slider ball, spin spinners.
    Channels alternate K1/K2 so press edges always exist."""
    frames = [StdFrame(time_ms=0, x=256.0, y=192.0, keys=0)]
    for i, obj in enumerate(sorted(bm.hit_objects,
                                   key=lambda o: o.get_start_time())):
        key = KEY_K1 if i % 2 == 0 else KEY_K2
        start, end = obj.get_start_time(), obj.get_end_time()
        if isinstance(obj, Spinner):
            t = start
            while t <= end:
                a = 2.0 * math.pi * 0.008 * (t - start)
                frames.append(StdFrame(
                    time_ms=int(t), x=256.0 + 50.0 * math.cos(a),
                    y=192.0 + 50.0 * math.sin(a), keys=key))
                t += 5.0
        elif isinstance(obj, Slider):
            t = start
            while t <= end:
                x, y = obj.get_stacked_position_at(t, bm.diff)
                frames.append(StdFrame(time_ms=int(t), x=x, y=y, keys=key))
                t += 5.0
            x, y = obj.get_stacked_position_at(end, bm.diff)
            frames.append(StdFrame(time_ms=int(end), x=x, y=y, keys=key))
        else:
            x, y = obj.get_stacked_start_position(bm.diff)
            frames.append(StdFrame(time_ms=int(start), x=x, y=y, keys=key))
            frames.append(StdFrame(time_ms=int(start) + 30, x=x, y=y,
                                   keys=key))
        # release frame so the next press is a fresh edge
        lx, ly = frames[-1].x, frames[-1].y
        frames.append(StdFrame(time_ms=frames[-1].time_ms + 1,
                               x=lx, y=ly, keys=0))
    return frames


def _sim(bm):
    return StdRuleset(bm, _perfect_replay_frames(bm), None).run()


def _write_wav(path: Path, n: int = 480, value: float = 0.25) -> Path:
    """Tiny valid float32 stereo wav (ffmpeg-decodable)."""
    data = np.full((n, 2), value, dtype="<f4").tobytes()
    with open(path, "wb") as fh:
        fh.write(b"RIFF")
        fh.write(struct.pack("<I", 36 + len(data)))
        fh.write(b"WAVEfmt ")
        fh.write(struct.pack("<IHHIIHH", 16, 3, 2, SAMPLE_RATE,
                             SAMPLE_RATE * 8, 8, 32))
        fh.write(b"data")
        fh.write(struct.pack("<I", len(data)))
        fh.write(data)
    return path


# --- §3.4 bit routing + layered semantics -------------------------------------------

def test_bits_zero_plays_hitnormal_only():
    assert sounds_for_bits(0, layered=False) == [("hitnormal", False)]
    assert sounds_for_bits(0, layered=True) == [("hitnormal", False)]


def test_bits_addition_sounds_use_addition_set():
    got = sounds_for_bits(2 | 4 | 8, layered=False)
    assert got == [("hitwhistle", True), ("hitfinish", True),
                   ("hitclap", True)]


def test_layered_adds_hitnormal_under_additions():
    got = sounds_for_bits(2, layered=True)
    assert got == [("hitnormal", False), ("hitwhistle", True)]
    # bit 1 forces hitnormal even unlayered
    got = sounds_for_bits(1 | 8, layered=False)
    assert got == [("hitnormal", False), ("hitclap", True)]


# --- volume floor ---------------------------------------------------------------------

def test_volume_floor_and_extras_override():
    quiet = TimingPoint(sample_volume=0.02)
    assert resolve_volume(quiet) == 0.08                  # §3.4 floor
    assert resolve_volume(quiet, extras_volume=0.5) == 0.5
    loud = TimingPoint(sample_volume=3.0)
    assert resolve_volume(loud) == 1.0                    # cap


# --- sample resolution chain ------------------------------------------------------------

def test_chain_skin_beats_fallback_beats_synth():
    skin_d = Path(tempfile.mkdtemp(prefix="hs_skin_"))
    fb_d = Path(tempfile.mkdtemp(prefix="hs_fb_"))
    _write_wav(skin_d / "normal-hitnormal.wav", value=0.5)
    _write_wav(fb_d / "normal-hitnormal.wav", value=0.1)
    _write_wav(fb_d / "soft-hitclap.wav", value=0.2)
    skin = Skin(skin_dir=skin_d, fallback_dir=fb_d)
    bank = SampleBank(skin=skin)
    pcm, src = bank.get(1, "hitnormal", 0)
    assert src == "skin" and abs(float(pcm.max()) - 0.5) < 0.02
    pcm, src = bank.get(2, "hitclap", 0)                  # FALLBACK source
    assert src == "skin" and abs(float(pcm.max()) - 0.2) < 0.02
    pcm, src = bank.get(3, "hitfinish", 0)                # nothing on disk
    assert src == "synth" and len(pcm) > 0


def test_beatmap_custom_index_override():
    skin_d = Path(tempfile.mkdtemp(prefix="hs_skin_"))
    map_d = Path(tempfile.mkdtemp(prefix="hs_map_"))
    _write_wav(skin_d / "soft-hitclap.wav", value=0.1)
    _write_wav(map_d / "soft-hitclap2.wav", value=0.4)
    _write_wav(map_d / "soft-hitwhistle.wav", value=0.3)  # index-1 bare name
    skin = Skin(skin_dir=skin_d)
    bank = SampleBank(skin=skin, beatmap_dir=map_d)
    _, src = bank.get(2, "hitclap", 2)                    # indexed override
    assert src == "beatmap"
    _, src = bank.get(2, "hitclap", 1)                    # no bare map file
    assert src == "skin"
    _, src = bank.get(2, "hitclap", 0)                    # 0 = never beatmap
    assert src == "skin"
    _, src = bank.get(2, "hitwhistle", 1)                 # bare name = idx 1
    assert src == "beatmap"


def test_ignore_beatmap_samples_toggle():
    map_d = Path(tempfile.mkdtemp(prefix="hs_map_"))
    _write_wav(map_d / "normal-hitnormal.wav", value=0.4)
    bank = SampleBank(skin=None, beatmap_dir=map_d,
                      use_beatmap_samples=False)
    _, src = bank.get(1, "hitnormal", 1)
    assert src == "synth"                                 # beatmap ignored
    bank2 = SampleBank(skin=None, beatmap_dir=map_d)
    _, src = bank2.get(1, "hitnormal", 1)
    assert src == "beatmap"


def test_zero_byte_sample_means_silence():
    skin_d = Path(tempfile.mkdtemp(prefix="hs_skin_"))
    (skin_d / "normal-hitnormal.wav").write_bytes(b"")
    bank = SampleBank(skin=Skin(skin_dir=skin_d))
    pcm, src = bank.get(1, "hitnormal", 0)
    assert src == "skin" and float(np.abs(pcm).max()) == 0.0


def test_synth_samples_deterministic_and_stereo():
    for name in ("normal-hitnormal", "soft-hitclap", "drum-hitfinish",
                 "normal-slidertick", "soft-sliderslide", "spinnerspin"):
        a, b = synth_sample(name), synth_sample(name)
        assert a.shape == b.shape and a.shape[1] == 2
        assert a.dtype == np.float32
        assert np.array_equal(a, b)
        assert float(np.abs(a).max()) > 0.0


# --- the two synth banks + the league rule (owner 2026-07-08) -------------------------

def test_synth_style_league_rule():
    """The synth-default bank follows the SAME league as the HUD
    visuals: skinless → argon; a custom skin's gaps → legacy;
    --legacy-defaults → legacy always."""
    assert synth_style_for(False, False) == "argon"       # skinless
    assert synth_style_for(True, False) == "legacy"       # custom skin
    assert synth_style_for(False, True) == "legacy"       # legacy_defaults
    assert synth_style_for(True, True) == "legacy"


def test_legacy_synth_bank_deterministic_distinct_and_full_surface():
    """The legacy bank covers the full §3.4 surface, stays deterministic
    stereo float32, and is a genuinely DIFFERENT sound family from the
    argon bank (per name); soft/drum are variations, not copies."""
    names = ["spinnerspin", "spinnerbonus"]
    for s in ("normal", "soft", "drum"):
        for snd in ("hitnormal", "hitwhistle", "hitfinish", "hitclap",
                    "slidertick", "sliderslide", "sliderwhistle"):
            names.append(f"{s}-{snd}")
    for name in names:
        a = synth_sample(name, style="legacy")
        b = synth_sample(name, style="legacy")
        assert a.shape == b.shape and a.shape[1] == 2
        assert a.dtype == np.float32
        assert np.array_equal(a, b)
        assert float(np.abs(a).max()) > 0.0
        ar = synth_sample(name, style="argon")
        assert a.shape != ar.shape or not np.array_equal(a, ar)
    # set variants differ from the normal set (tonal/filter variations)
    for snd in ("hitnormal", "hitwhistle", "hitfinish", "hitclap"):
        n = synth_sample(f"normal-{snd}", style="legacy")
        for s in ("soft", "drum"):
            v = synth_sample(f"{s}-{snd}", style="legacy")
            assert n.shape != v.shape or not np.array_equal(n, v)
    # unknown names still make a sound in both banks
    assert float(np.abs(synth_sample("mystery", style="legacy")).max()) > 0


def test_sample_bank_selects_the_league_bank():
    """SampleBank(synth_style=…) hands out the selected bank's synth
    defaults when nothing on disk provides the sample."""
    lg = SampleBank(skin=None, synth_style="legacy")
    ar = SampleBank(skin=None)                      # default = argon
    pcm_lg, src_lg = lg.get(1, "hitnormal", 0)
    pcm_ar, src_ar = ar.get(1, "hitnormal", 0)
    assert src_lg == "synth" and src_ar == "synth"
    assert lg.synth_style == "legacy" and ar.synth_style == "argon"
    assert np.array_equal(pcm_lg, synth_sample("normal-hitnormal",
                                               style="legacy"))
    assert np.array_equal(pcm_ar, synth_sample("normal-hitnormal"))
    assert (pcm_lg.shape != pcm_ar.shape
            or not np.array_equal(pcm_lg, pcm_ar))
    # a skin-shipped sample still wins over the bank (chain unchanged)
    skin_d = Path(tempfile.mkdtemp(prefix="hs_lg_"))
    _write_wav(skin_d / "normal-hitnormal.wav", value=0.5)
    bank = SampleBank(skin=Skin(skin_dir=skin_d), synth_style="legacy")
    pcm, src = bank.get(1, "hitnormal", 0)
    assert src == "skin" and abs(float(pcm.max()) - 0.5) < 0.02


# --- event collection -----------------------------------------------------------------

def test_circle_events_sets_volume_no_sound_on_miss():
    bm = _map("256,192,1000,1,8,0:0:0:0:\n")
    sim = _sim(bm)
    assert sim.final_counts == (1, 0, 0, 0)
    ones, loops = collect_hitsound_events(bm, sim, layered=True)
    got = {(o.sound, o.set_id) for o in ones}
    assert got == {("hitnormal", 2), ("hitclap", 2)}      # point set = soft
    assert all(abs(o.volume - 0.6) < 1e-9 for o in ones)  # point vol 60
    assert all(o.time_ms == 1000.0 for o in ones)
    assert loops == []
    # miss → silence: an empty replay judges the circle as a miss
    missed = StdRuleset(bm, [], None).run()
    assert missed.final_counts == (0, 0, 0, 1)
    ones2, _ = collect_hitsound_events(bm, missed)
    assert ones2 == []


def test_circle_extras_override_set_index_volume():
    bm = _map("256,192,1000,1,2,3:1:5:45:\n")   # whistle; extras drum/norm/5/45%
    ones, _ = collect_hitsound_events(bm, _sim(bm), layered=False)
    assert len(ones) == 1
    o = ones[0]
    assert o.sound == "hitwhistle"
    assert o.set_id == 1          # ADDITION set: extras additionSet=1 normal
    assert o.index == 5           # extras customIndex overrides point's
    assert abs(o.volume - 0.45) < 1e-9


def test_slider_edges_ticks_and_loops():
    # L slider 0→300px @0.2px/ms: head 1000, ticks 1500/2000, tail 2500.
    # body hitSound=2 (whistle → sliderwhistle loop), edgeSounds 4|2,
    # edgeSets 1:2|3:0
    bm = _map("0,192,1000,2,2,L|300:192,1,300,4|2,1:2|3:0,0:0:0:0:\n")
    obj = bm.hit_objects[0]
    assert isinstance(obj, Slider)
    sim = _sim(bm)
    assert sim.final_counts == (1, 0, 0, 0)
    v = sim.verdict_for(obj)
    assert v.hit_time == 1000.0 and all(p.hit for p in v.parts)
    ones, loops = collect_hitsound_events(bm, sim, layered=True)
    head = [o for o in ones if o.time_ms == 1000.0]
    assert {(o.sound, o.set_id) for o in head} == {
        ("hitnormal", 1),         # head edge base set = 1 (normal)
        ("hitfinish", 2),         # head addition set = 2 (soft)
    }
    ticks = [o for o in ones if o.sound == "slidertick"]
    assert [o.time_ms for o in ticks] == [1500.0, 2000.0]
    assert all(o.set_id == 2 for o in ticks)              # point set soft
    tail = [o for o in ones if o.time_ms == 2500.0]
    assert {(o.sound, o.set_id) for o in tail} == {
        ("hitnormal", 3),         # tail edge base set = 3 (drum)
        ("hitwhistle", 3),        # tail addition 0 → base
    }
    # loops: sliderslide + sliderwhistle tile the whole tracked slide
    assert v.tracking == [(1000.0, 2500.0)]
    kinds = {(lp.sound, lp.t0, lp.t1, lp.set_id) for lp in loops}
    assert ("sliderslide", 1000.0, 2500.0, 2) in kinds
    assert ("sliderwhistle", 1000.0, 2500.0, 2) in kinds
    assert len(loops) == 2


def test_loop_split_at_timing_points():
    timing = "0,500,4,2,1,60,1,0\n1750,-100,4,3,2,30,0,0"
    bm = _map("0,192,1000,2,0,L|300:192,1,300,0|0,0:0|0:0,0:0:0:0:\n",
              timing=timing)
    _, loops = collect_hitsound_events(bm, _sim(bm))
    slides = sorted([lp for lp in loops if lp.sound == "sliderslide"],
                    key=lambda lp: lp.t0)
    assert [(lp.t0, lp.t1) for lp in slides] == [(1000.0, 1750.0),
                                                 (1750.0, 2500.0)]
    assert (slides[0].set_id, slides[0].index, slides[0].volume) == (2, 1, 0.6)
    assert (slides[1].set_id, slides[1].index, slides[1].volume) == (3, 2, 0.3)


def test_spinner_loop_and_clear_hit():
    bm = _map("256,192,1000,12,4,3000,0:0:0:0:\n")
    obj = bm.hit_objects[0]
    assert isinstance(obj, Spinner)
    sim = _sim(bm)
    assert sim.final_counts == (1, 0, 0, 0)               # spinner cleared
    ones, loops = collect_hitsound_events(bm, sim)
    spin = [lp for lp in loops if lp.sound == "spinnerspin"]
    assert len(spin) == 1 and (spin[0].t0, spin[0].t1) == (1000.0, 3000.0)
    assert spin[0].set_id == 0                            # un-prefixed name
    end_hits = {(o.sound, o.time_ms) for o in ones}
    assert ("hitfinish", 3000.0) in end_hits              # cleared at end


# --- mixing ---------------------------------------------------------------------------

def test_mix_hitsounds_deterministic_and_clipped():
    bank = SampleBank()                                   # synth-only
    ones = [OneShot(100.0, "hitnormal", 1, 0, 1.0),
            OneShot(500.0, "hitclap", 2, 0, 0.5),
            OneShot(10_000_000.0, "hitclap", 2, 0, 1.0)]  # beyond end: clipped
    loops = [Loop(200.0, 900.0, "sliderslide", 1, 0, 0.6)]
    bufs = []
    for _ in range(2):
        mixer = AudioMixer(1000.0)
        stats = mix_hitsounds(mixer, bank, ones, loops)
        bufs.append(mixer.buf.copy())
        assert stats.oneshots == 3
        assert abs(stats.loop_ms - 700.0) < 1e-9
        assert stats.peak_after > stats.peak_before == 0.0
    assert np.array_equal(bufs[0], bufs[1])
    # the loop actually tiles: energy present across the window
    seg = bufs[0][int(0.25 * SAMPLE_RATE):int(0.85 * SAMPLE_RATE)]
    assert float(np.abs(seg).max()) > 0.0


def test_mix_event_fully_before_window_is_noop():
    # regression: a hit long before --start used to wrap the buffer slice
    # (negative end index) and crash the broadcast in mix_at
    bank = SampleBank()
    mixer = AudioMixer(10_000.0)
    mix_hitsounds(mixer, bank,
                  [OneShot(500.0, "hitclap", 2, 0, 1.0)],
                  [Loop(100.0, 400.0, "sliderslide", 1, 0, 1.0)],
                  start_ms=30_000.0)                      # 30 s clip start
    assert float(np.abs(mixer.buf).max()) == 0.0


def test_mix_rate_mod_wall_time():
    bank = SampleBank()
    mixer = AudioMixer(1000.0)                            # 1 s wall
    # DT (speed 1.5): a hit at map 1200 ms lands at wall 800 ms
    mix_hitsounds(mixer, bank, [OneShot(1200.0, "hitnormal", 1, 0, 1.0)],
                  [], speed=1.5)
    i = int(0.8 * SAMPLE_RATE)
    assert float(np.abs(mixer.buf[i:i + 2000]).max()) > 0.0
    assert float(np.abs(mixer.buf[:i - 2000]).max()) == 0.0
