"""§2.4 formula spot-checks (plain asserts; run via tests/run_all.py or pytest)."""
from osu_std_renderer.beatmap.difficulty import (Difficulty, Mods,
                                                 diff_from_rate,
                                                 difficulty_rate)


def test_difficulty_rate_anchors():
    # AR → preempt anchors: AR0=1800, AR5=1200, AR10=450
    assert difficulty_rate(0, 1800, 1200, 450) == 1800
    assert difficulty_rate(5, 1800, 1200, 450) == 1200
    assert difficulty_rate(10, 1800, 1200, 450) == 450
    assert difficulty_rate(9, 1800, 1200, 450) == 600


def test_diff_from_rate_inverse():
    for ar in (0.0, 3.7, 5.0, 8.0, 9.3, 10.0):
        pre = difficulty_rate(ar, 1800, 1200, 450)
        assert abs(diff_from_rate(pre, 1800, 1200, 450) - ar) < 1e-4


def test_radius_and_windows():
    d = Difficulty(ar=9, od=7, cs=4, hp=5)
    assert abs(d.circle_radius_u - 36.48) < 1e-6
    assert abs(d.circle_radius - 36.48 * 1.00041) < 1e-6
    assert d.preempt == 600
    assert abs(d.time_fade_in - 400) < 1e-9  # preempt_u 600 > 450 → full 400
    # OD7 windows: 50→150-10*... DifficultyRate(7,200,150,100)=130 etc.
    assert abs(d.hit50_u - 130) < 1e-6
    assert abs(d.hit100_u - 84) < 1e-6
    assert abs(d.hit300_u - 38) < 1e-6


def test_od10_windows():
    d = Difficulty(ar=10, od=10, cs=5, hp=5)
    assert (d.hit50_u, d.hit100_u, d.hit300_u) == (100, 60, 20)
    assert d.preempt == 450


def test_hard_rock():
    d = Difficulty(ar=8, od=8, cs=4, hp=6)
    d.set_mods(Mods.HARD_ROCK)
    assert abs(d.ar - min(8 * 1.4, 10)) < 1e-9   # 10 (clamped)
    assert abs(d.cs - 4 * 1.3) < 1e-9            # 5.2
    assert abs(d.od - 10) < 1e-9
    assert d.preempt == 450


def test_easy():
    d = Difficulty(ar=8, od=8, cs=4, hp=6)
    d.set_mods(Mods.EASY)
    assert (d.ar, d.od, d.cs, d.hp) == (4, 4, 2, 3)


def test_dt_ht_speed_and_reals():
    d = Difficulty(ar=9, od=9, cs=4, hp=5)
    d.set_mods(Mods.DOUBLE_TIME)
    assert d.speed == 1.5
    # AR9 preempt 600 → at DT effective 400ms → ARreal ≈ 10.33
    assert abs(d.ar_real - diff_from_rate(600 / 1.5, 1800, 1200, 450)) < 1e-9
    assert d.ar_real > 10
    d.set_mods(Mods.HALF_TIME)
    assert d.speed == 0.75
    d.set_mods(Mods.NIGHTCORE)          # NC implies DT
    assert d.check_mod(Mods.DOUBLE_TIME)
    assert d.speed == 1.5


def test_float32_quirk_applied():
    # 9.3 is not float32-representable; the cast must change the result
    raw = 1200 - 750 * (9.3 - 5) / 5
    assert difficulty_rate(9.3, 1800, 1200, 450) != raw
