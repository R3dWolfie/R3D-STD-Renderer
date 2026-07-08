"""HP model tests — the lazer OsuHealthProcessor/DrainingHealthProcessor
port (ruleset/health.py): drain-rate solve basics, gains on hits, the miss
drop, clamping, break handling and the timeline's stateless queries."""
from __future__ import annotations

import tempfile
from pathlib import Path

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.replay.replay import KEY_K1, StdFrame
from osu_std_renderer.ruleset import HealthTimeline, StdRuleset
from osu_std_renderer.ruleset.health import compute_drain_rate

MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
StackLeniency: 0.7
Mode: 0

[Metadata]
Title:HpTest
Artist:Unit
Creator:R3D
Version:Hp

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:7
ApproachRate:9
SliderMultiplier:1.0
SliderTickRate:1

[TimingPoints]
0,500,4,2,1,60,1,0

[HitObjects]
"""


def _map(objects: str, events: str = "") -> object:
    d = Path(tempfile.mkdtemp(prefix="stdhp_"))
    p = d / "test.osu"
    body = MAP_HEADER
    if events:
        body = body.replace("[HitObjects]", f"[Events]\n{events}\n[HitObjects]")
    p.write_text(body + objects, encoding="utf-8")
    return load_full(p)


def _frames(entries) -> list[StdFrame]:
    fr = [StdFrame(time_ms=0, x=0.0, y=0.0, keys=0)]
    fr += [StdFrame(time_ms=t, x=float(x), y=float(y), keys=k)
           for t, x, y, k in entries]
    return fr


def _run(bm, clicks):
    entries = []
    for t, x, y in clicks:
        entries += [(t, x, y, KEY_K1), (t + 20, x, y, 0)]
    if not clicks:
        entries = [(100, 0, 0, 0), (99000, 0, 0, 0)]
    else:
        entries.append((clicks[-1][0] + 2000, 0, 0, 0))
    return StdRuleset(bm, _frames(entries)).run()


THREE = ("100,100,1000,1,0,0:0:0:0:\n"
         "200,100,2000,1,0,0:0:0:0:\n"
         "300,100,3000,1,0,0:0:0:0:\n")


# --- compute_drain_rate (DrainingHealthProcessor.ComputeDrainRate) -------------------

def test_drain_rate_binary_search_hits_target():
    """A long uniform perfect play must drain to ~the target minimum."""
    increases = [(i * 1000.0, 0.03) for i in range(60)]
    target = 0.9
    rate = compute_drain_rate(increases, [], 0.0, target)
    assert rate > 0.0
    # replay the drain: lowest health should land within the 1% error
    hp, lowest = 1.0, 1.0
    last = 0.0
    for t, amt in increases:
        hp -= (t - last) * rate
        lowest = min(lowest, hp)
        hp = min(1.0, hp + amt)
        last = t
    assert abs(lowest - target) <= 0.011


def test_drain_rate_trivial_cases():
    assert compute_drain_rate([], [], 0.0, 0.9) == 0.0
    assert compute_drain_rate([(0.0, 0.03)], [], 0.0, 0.9) == 0.0


def test_drain_rate_breaks_pause_drain():
    """The same schedule with the long gap covered by a break needs a
    HIGHER rate to reach the target (less drain time available)."""
    increases = ([(i * 500.0, 0.03) for i in range(20)]
                 + [(30000.0 + i * 500.0, 0.03) for i in range(20)])
    no_break = compute_drain_rate(increases, [], 0.0, 0.9)
    with_break = compute_drain_rate(increases, [(9500.0, 30000.0)], 0.0, 0.9)
    assert with_break >= no_break


# --- HealthTimeline over a real sim ---------------------------------------------------

def test_hp_starts_full_gains_on_hits_and_clamps():
    bm = _map(THREE)
    sim = _run(bm, [(1000, 100, 100), (2000, 200, 100), (3000, 300, 100)])
    hp = HealthTimeline(bm, sim)
    assert hp.hp_at(-1000.0) == 1.0
    assert hp.hp_at(999.0) <= 1.0
    # a hit gains health (clamped at 1)
    before = hp.hp_at(1999.0)
    after = hp.hp_at(2001.0)
    assert after >= before
    assert all(v <= 1.0 + 1e-9 for v in hp.bp_values)
    assert all(v >= 0.0 for v in hp.bp_values)
    assert not hp.miss_events
    assert hp.gain_events                      # hits flashed
    assert hp.final_hp > 0.5


def test_hp_miss_drops_and_registers_miss_event():
    bm = _map(THREE)
    sim = _run(bm, [(1000, 100, 100), (2000, 200, 100)])   # third missed
    hp = HealthTimeline(bm, sim)
    deadline = 3000.0 + 129.5                  # OD7 meh window close
    before = hp.hp_at(deadline - 1.0)
    after = hp.hp_at(deadline + 1.0)
    assert after < before                      # the miss loss (HP5 → −0.125)
    assert abs((before - after) - 0.125) < 0.02
    assert len(hp.miss_events) == 1
    m = hp.last_miss_at(1e9)
    assert m is not None and abs(m[0] - deadline) < 1e-6
    assert hp.last_miss_at(2000.0) is None


def test_hp_drain_between_objects_and_no_drain_before_first():
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "200,100,9000,1,0,0:0:0:0:\n")
    sim = _run(bm, [(1000, 100, 100), (9000, 200, 100)])
    hp = HealthTimeline(bm, sim)
    assert hp.hp_at(500.0) == 1.0              # before drain start
    if hp.drain_rate > 0:
        assert hp.hp_at(8000.0) < hp.hp_at(1100.0)   # draining in the gap


def test_hp_break_period_stops_drain():
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "200,100,20000,1,0,0:0:0:0:\n",
              events="2,2000,18000")
    sim = _run(bm, [(1000, 100, 100), (20000, 200, 100)])
    hp = HealthTimeline(bm, sim)
    # inside the object-snapped no-drain window the level is flat
    assert abs(hp.hp_at(5000.0) - hp.hp_at(15000.0)) < 1e-9
