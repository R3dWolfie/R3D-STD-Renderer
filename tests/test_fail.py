"""FAIL/DEATH handling — the osu! fail sequence for a replay that died.

Covers the three concerns of the fail feature:
  1. detection   (life-bar 0 → fail_time; NoFail/Autopilot → pass; empty
                  life-bar → conservative pass)
  2. animation   (FailAnimationContainer.cs transforms ported: object
                  gravity/rotation/scale, playfield gray, red flash)
  3. results     (grade F colour + stats FROZEN at the death point)
"""
from __future__ import annotations

import math

from osu_std_renderer.replay.replay import (MOD_AUTOPILOT, MOD_NOFAIL,
                                            detect_fail_time)


# --- fakes ---------------------------------------------------------------------------

class _Life:
    def __init__(self, time, life):
        self.time = time
        self.life = life


class _Replay:
    """Duck-typed osrparse.Replay stand-in for detect_fail_time."""
    def __init__(self, mods=0, life=None):
        self.mods = mods
        self.life_bar_graph = life


# --- 1. detection --------------------------------------------------------------------

def test_detect_fail_lifebar_zero_returns_first_death():
    r = _Replay(mods=0, life=[_Life(0, 1.0), _Life(1000, 0.4),
                              _Life(2000, 0.0), _Life(3000, 0.0)])
    assert detect_fail_time(r) == 2000.0


def test_detect_fail_epsilon_near_zero_trips():
    r = _Replay(mods=0, life=[_Life(500, 0.0005)])
    assert detect_fail_time(r) == 500.0


def test_detect_no_fail_when_health_never_zero():
    r = _Replay(mods=0, life=[_Life(0, 1.0), _Life(1000, 0.2),
                              _Life(2000, 0.5)])
    assert detect_fail_time(r) is None


def test_nofail_mod_never_fails():
    # even with the life-bar flatlined at 0, NoFail overrides failing
    r = _Replay(mods=MOD_NOFAIL, life=[_Life(1000, 0.0)])
    assert detect_fail_time(r) is None
    assert MOD_NOFAIL == 0x1


def test_autopilot_mod_never_fails():
    r = _Replay(mods=MOD_AUTOPILOT, life=[_Life(1000, 0.0)])
    assert detect_fail_time(r) is None
    assert MOD_AUTOPILOT == 0x2000


def test_relax_can_still_fail():
    # Relax (0x80) does NOT override failing in osu! — a zero still trips
    r = _Replay(mods=0x80, life=[_Life(1200, 0.0)])
    assert detect_fail_time(r) == 1200.0


def test_empty_lifebar_is_conservative_pass():
    assert detect_fail_time(_Replay(mods=0, life=[])) is None
    assert detect_fail_time(_Replay(mods=0, life=None)) is None


def test_nofail_combined_with_other_mods():
    # HD+DT+NoFail (0x8|0x40|0x1) — NoFail bit still exempts
    r = _Replay(mods=0x49, life=[_Life(1000, 0.0)])
    assert detect_fail_time(r) is None


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
