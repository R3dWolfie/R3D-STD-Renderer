"""render/pp.py — the rosu-pp gradual timeline (endpoint honesty against
the full calculator), the rolling query, strains + the density proxy,
and the fail-soft paths. Needs rosu-pp-py in the venv (installed); the
no-rosu path is simulated by monkeypatching the module handle."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import osu_std_renderer.render.pp as pp_mod
from osu_std_renderer.render.pp import (build_pp_timeline,
                                        build_strain_series, pp_at,
                                        rosu_available, strain_proxy)
from osu_std_renderer.ruleset import JudgmentKind

_OSU = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 0

[Metadata]
Title:PP Test
Artist:T
Creator:t
Version:x

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
100,100,1000,1,0,0:0:0:0:
200,100,1500,1,0,0:0:0:0:
300,100,2000,1,0,0:0:0:0:
200,200,2500,1,0,0:0:0:0:
"""


def _write_map(tmp: Path) -> Path:
    p = tmp / "pp_test.osu"
    p.write_text(_OSU, encoding="utf-8")
    return p


def _perfect_sim(n=4, lazer=False):
    events = [SimpleNamespace(time_ms=1000.0 + 500.0 * i,
                              kind=JudgmentKind.HIT300, object_id=i)
              for i in range(n)]
    timeline = [(e.time_ms, i + 1) for i, e in enumerate(events)]
    verdicts = {i: SimpleNamespace(start_time=e.time_ms, end_time=e.time_ms,
                                   parts=[])
                for i, e in enumerate(events)}
    return SimpleNamespace(events=events, combo_timeline=timeline,
                           final_max_combo=n, lazer=lazer,
                           verdicts=verdicts)


def test_rosu_is_available_in_the_venv():
    assert rosu_available()          # installed for the pp counter


def test_gradual_endpoint_agrees_with_full_calc():
    with tempfile.TemporaryDirectory() as td:
        osu = _write_map(Path(td))
        res = build_pp_timeline(osu, 0, _perfect_sim())
        assert res is not None
        pts, info = res
        assert info.n_points == 4
        # per-judgment points, time-sorted, pp grows on a clean run
        assert [t for t, _ in pts] == sorted(t for t, _ in pts)
        assert pts[-1][1] > pts[0][1] > 0.0
        # ENDPOINT HONESTY: gradual's last state == the one-shot calc
        assert abs(info.gradual_end - info.full_calc) < 0.05


def test_lazer_replays_use_lazer_scoring():
    """A lazer sim (game_version ≥ 30M) routes through Difficulty(
    lazer=True) + the slider-stat ScoreState — endpoint still agrees."""
    with tempfile.TemporaryDirectory() as td:
        osu = _write_map(Path(td))
        res_stable = build_pp_timeline(osu, 0, _perfect_sim())
        res_lazer = build_pp_timeline(osu, 0, _perfect_sim(lazer=True))
        assert res_stable is not None and res_lazer is not None
        _, info_l = res_lazer
        assert abs(info_l.gradual_end - info_l.full_calc) < 0.05
        # circles-only map: both models judge the same objects; the pp
        # models differ slightly but both are positive and finite
        assert info_l.full_calc > 0.0


def test_pp_timeline_fail_soft_paths():
    # unparseable map → None (never raises)
    assert build_pp_timeline(Path("/nonexistent.osu"), 0,
                             _perfect_sim()) is None
    # no rosu binding → None (counter hides)
    saved = pp_mod._rosu
    try:
        pp_mod._rosu = None
        with tempfile.TemporaryDirectory() as td:
            assert build_pp_timeline(_write_map(Path(td)), 0,
                                     _perfect_sim()) is None
            assert build_strain_series(_write_map(Path(td)), 0, 1.0,
                                       0.0) is None
    finally:
        pp_mod._rosu = saved
    # empty sim → None
    empty = SimpleNamespace(events=[], combo_timeline=[], final_max_combo=0)
    with tempfile.TemporaryDirectory() as td:
        assert build_pp_timeline(_write_map(Path(td)), 0, empty) is None


def test_pp_at_rolls_between_points():
    pts = [(1000.0, 10.0), (2000.0, 20.0)]
    times = [p[0] for p in pts]
    assert pp_at(pts, times, 999.0) == 0.0
    assert abs(pp_at(pts, times, 1125.0) - 5.0) < 1e-9     # mid-roll from 0
    assert pp_at(pts, times, 1500.0) == 10.0               # settled
    assert abs(pp_at(pts, times, 2100.0) - 14.0) < 1e-9    # rolling 10→20
    assert pp_at(pts, times, 3000.0) == 20.0


def test_rosu_strains_shape():
    with tempfile.TemporaryDirectory() as td:
        st = build_strain_series(_write_map(Path(td)), 0, 1.0, 1000.0)
        assert st is not None and st.source == "rosu"
        assert st.section_ms > 0 and len(st.values) >= 1
        assert all(v >= 0.0 for v in st.values)
        assert st.first_t == 1000.0 and st.speed == 1.0


def test_strain_proxy_density_shape():
    starts = [0.0, 100.0, 200.0, 5000.0]
    ends = [50.0, 150.0, 1200.0, 5100.0]
    st = strain_proxy(starts, ends, window_ms=1000.0)
    assert st is not None and st.source == "density-proxy"
    assert st.section_ms == 1000.0 and st.speed == 1.0
    # bucket 0 holds the 3-object cluster; the smoothed peak sits there
    assert st.values[0] == max(st.values)
    assert len(st.values) == 6                     # 0..5100 ms span
    assert strain_proxy([], []) is None
