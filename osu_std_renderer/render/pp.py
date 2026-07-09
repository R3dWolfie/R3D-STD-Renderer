"""Live pp + strain data — §4.6 PPCounter / StrainGraph on rosu-pp
(the bot's versus_telemetry.py GradualPerformance pattern, ported).

PP TIMELINE (build_pp_timeline)
  rosu-pp's GradualPerformance consumes one ScoreState per hit object IN
  MAP ORDER; we replay the ruleset's judgment events sorted by object id,
  accumulating 300/100/50/miss counts and reading the running MAX combo
  off the part-level combo timeline (prefix max at the event time — ticks
  and repeats peak combo between object judgments). Each `next()` yields
  the pp-so-far, anchored at the judgment's DISPLAY time, so the HUD
  counter updates per judgment exactly like the score/acc counters.

  SCORING MODEL follows the replay: stable .osr (game_version <30M, the
  ruleset's sim.lazer=False) → Difficulty(lazer=False) + the plain
  300/100/50/miss ScoreState; lazer .osr → Difficulty(lazer=True) + the
  slider stats (large-tick hits = judged ticks+repeats, slider-end hits
  = judged tails, straight off the ruleset's verdict parts — the bot's
  versus_telemetry pattern).

  ENDPOINT HONESTY: the final gradual value is checked against a full
  rosu Performance calculation with the same end state; both numbers are
  returned so the CLI can print them (they agree to float noise — the
  same state machine computes both).

  FAIL-SOFT: rosu missing / map unparseable / mode mismatch / any rosu
  error → None; the pp counter simply hides (the preset stays accepted).

STRAINS (build_strain_series)
  rosu's Difficulty.strains() per-section aim+speed sums when available —
  section timestamps are in RATE-ADJUSTED ms, so the series carries the
  mod `speed` back out for map-time placement. When rosu is unavailable
  the OBJECT-DENSITY PROXY (strain_proxy — objects per second, smoothed)
  stands in; the returned `source` label says which one a render used
  (the CLI prints it, the report documents it).
"""
from __future__ import annotations

import bisect
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import rosu_pp_py as _rosu
except Exception:  # noqa: BLE001 — optional dependency, counter hides
    _rosu = None

from ..ruleset import JudgmentKind

PP_ROLL_MS = 250.0             # counter roll (matches the Argon counters)


def rosu_available() -> bool:
    return _rosu is not None


@dataclass
class PpInfo:
    """The honesty numbers for the CLI report line."""
    gradual_end: float
    full_calc: float
    n_points: int


def build_pp_timeline(osu_path: Path, mods: int, sim,
                      ) -> tuple[list[tuple[float, float]], PpInfo] | None:
    """[(display_time_ms, pp_so_far)] time-sorted + the endpoint check,
    or None when rosu/the map can't provide (fail-soft)."""
    if _rosu is None or sim is None or not sim.events:
        return None
    try:
        bm = _rosu.Beatmap(path=str(osu_path))
        if bm.mode != _rosu.GameMode.Osu:
            return None
        lazer = bool(getattr(sim, "lazer", False))
        diff = _rosu.Difficulty(mods=int(mods), lazer=lazer)
        grad = _rosu.GradualPerformance(diff, bm)

        # prefix max of the part-level combo timeline (ticks/repeats can
        # peak between object judgments)
        ct = sim.combo_timeline
        ct_times = [t for t, _ in ct]
        ct_maxes: list[int] = []
        m = 0
        for _, v in ct:
            m = max(m, v)
            ct_maxes.append(m)

        def max_combo_at(t: float) -> int:
            i = bisect.bisect_right(ct_times, t) - 1
            return ct_maxes[i] if i >= 0 else 0

        # lazer scoring wants the slider stats per object: judged
        # ticks+repeats (large ticks) and tails (slider ends), read off
        # the verdict parts in object order (verdicts sorted by start
        # time == the parser's hit_object_id order)
        part_stats: dict[int, tuple[int, int]] = {}
        if lazer and getattr(sim, "verdicts", None):
            ordered = sorted(sim.verdicts.values(),
                             key=lambda v: (v.start_time, v.end_time))
            for i, v in enumerate(ordered):
                lt = sum(1 for p in v.parts
                         if p.kind in ("tick", "repeat") and p.hit)
                te = sum(1 for p in v.parts
                         if p.kind == "tail" and p.hit)
                part_stats[i] = (lt, te)

        n300 = n100 = n50 = miss = ticks = tails = 0
        pts: list[tuple[float, float]] = []
        for ev in sorted(sim.events, key=lambda e: e.object_id):
            if ev.kind is JudgmentKind.HIT300:
                n300 += 1
            elif ev.kind is JudgmentKind.HIT100:
                n100 += 1
            elif ev.kind is JudgmentKind.HIT50:
                n50 += 1
            else:
                miss += 1
            if lazer:
                lt, te = part_stats.get(ev.object_id, (0, 0))
                ticks += lt
                tails += te
                state = _rosu.ScoreState(
                    max_combo=max_combo_at(ev.time_ms), n300=n300,
                    n100=n100, n50=n50, misses=miss,
                    osu_large_tick_hits=ticks, osu_small_tick_hits=0,
                    slider_end_hits=tails, n_geki=0, n_katu=0)
            else:
                state = _rosu.ScoreState(
                    max_combo=max_combo_at(ev.time_ms), n300=n300,
                    n100=n100, n50=n50, misses=miss)
            attrs = grad.next(state)
            if attrs is None:
                break
            pts.append((ev.time_ms, float(attrs.pp)))
        if not pts:
            return None
        pts.sort(key=lambda p: p[0])

        # endpoint: the same end state through the one-shot calculator
        kwargs = dict(mods=int(mods), lazer=lazer,
                      combo=int(sim.final_max_combo),
                      n300=n300, n100=n100, n50=n50, misses=miss)
        if lazer:
            kwargs.update(large_tick_hits=ticks, small_tick_hits=0,
                          slider_end_hits=tails, n_geki=0, n_katu=0)
        full = _rosu.Performance(**kwargs).calculate(bm)
        return pts, PpInfo(gradual_end=pts[-1][1],
                           full_calc=float(full.pp), n_points=len(pts))
    except Exception as e:  # noqa: BLE001 — pp is garnish, never fatal
        print(f"WARNING: pp timeline failed ({e}) — pp counter hidden",
              file=sys.stderr)
        return None


def pp_at(pts: list[tuple[float, float]], times: list[float], t: float,
          roll_ms: float = PP_ROLL_MS) -> float:
    """The displayed pp at time t: the last point's value rolled from the
    previous one over roll_ms (linear — the counters' quad-out is
    indistinguishable at 2-3 digit pp deltas)."""
    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return 0.0
    prev = pts[i - 1][1] if i > 0 else 0.0
    age = t - pts[i][0]
    if age >= roll_ms or roll_ms <= 0:
        return pts[i][1]
    return prev + (pts[i][1] - prev) * (age / roll_ms)


# --- strains -------------------------------------------------------------------------

@dataclass
class StrainSeries:
    """Per-section strain values for the graph: values[i] covers map time
    [first_t + i·section_ms·speed, …+section_ms·speed) (rosu sections are
    rate-adjusted; the proxy is built in map time with speed=1 baked in)."""
    values: list[float]
    section_ms: float
    first_t: float
    speed: float
    source: str                # "rosu" | "density-proxy"


def build_strain_series(osu_path: Path, mods: int, speed: float,
                        first_t: float) -> StrainSeries | None:
    """rosu aim+speed strain sums per section, or None (caller falls back
    to strain_proxy)."""
    if _rosu is None:
        return None
    try:
        bm = _rosu.Beatmap(path=str(osu_path))
        if bm.mode != _rosu.GameMode.Osu:
            return None
        st = _rosu.Difficulty(mods=int(mods)).strains(bm)
        aim = list(st.aim or [])
        spd = list(st.speed or [])
        n = max(len(aim), len(spd))
        if n == 0:
            return None
        vals = [(aim[i] if i < len(aim) else 0.0)
                + (spd[i] if i < len(spd) else 0.0) for i in range(n)]
        return StrainSeries(values=vals, section_ms=float(st.section_length),
                            first_t=first_t, speed=speed, source="rosu")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: rosu strains failed ({e}) — density proxy",
              file=sys.stderr)
        return None


def strain_proxy(starts: list[float], ends: list[float],
                 window_ms: float = 1000.0) -> StrainSeries | None:
    """The no-rosu fallback: per-second OBJECT DENSITY over the map
    (objects span start..end), smoothed with a 3-bucket moving average —
    an honest shape proxy, labelled as such."""
    if not starts:
        return None
    first, last = min(starts), max(ends)
    n = max(int((last - first) / window_ms) + 1, 1)
    vals = [0.0] * n
    for s, e in zip(starts, ends):
        i0 = int((s - first) / window_ms)
        i1 = int((e - first) / window_ms)
        for i in range(max(i0, 0), min(i1, n - 1) + 1):
            vals[i] += 1.0
    sm = [(vals[max(i - 1, 0)] + vals[i] + vals[min(i + 1, n - 1)]) / 3.0
          for i in range(n)]
    return StrainSeries(values=sm, section_ms=window_ms, first_t=first,
                        speed=1.0, source="density-proxy")


# --- performance breakdown (lazer results screen) ----------------------------------


@dataclass
class PerfBreakdown:
    """The PerformanceBreakdown row for the lazer results screen
    (osu.Game/Screens/Ranking/Statistics/PerformanceBreakdown.cs): each
    rosu pp component as achieved ÷ SS-play, plus the achieved/maximum pp
    pair for the label."""
    aim_pct: float                 # achieved pp_aim / SS pp_aim, clamped
    speed_pct: float
    acc_pct: float
    achieved_pp: float
    max_pp: float


def component_pct(achieved, maximum) -> float:
    """A pp component as a fraction of the SS-play component, clamped to
    [0,1] (the PerformanceBreakdown bar fill). A zero/absent maximum → 0."""
    a = float(achieved or 0.0)
    b = float(maximum or 0.0)
    return 0.0 if b <= 0.0 else max(0.0, min(1.0, a / b))


def build_performance_breakdown(osu_path, mods: int, sim, counts,
                                final_max_combo: int) -> PerfBreakdown | None:
    """Achieved-vs-maximum pp components (lazer's PerformanceBreakdown):
    the achieved play's rosu pp_aim/pp_speed/pp_accuracy over the SAME
    map+mods SS play's components. The SS reference is rosu's default
    Performance (no state = a perfect play). lazer slider stats feed the
    achieved state exactly as build_pp_timeline does. None fail-soft."""
    if _rosu is None or sim is None:
        return None
    try:
        bm = _rosu.Beatmap(path=str(osu_path))
        if bm.mode != _rosu.GameMode.Osu:
            return None
        lazer = bool(getattr(sim, "lazer", False))
        n300, n100, n50, miss = counts
        kwargs = dict(mods=int(mods), lazer=lazer, n300=int(n300),
                      n100=int(n100), n50=int(n50), misses=int(miss),
                      combo=int(final_max_combo))
        if lazer:
            large_ticks = tails = 0
            for v in sim.verdicts.values():
                for p in v.parts:
                    if p.kind in ("tick", "repeat") and p.hit:
                        large_ticks += 1
                    elif p.kind == "tail" and p.hit:
                        tails += 1
            kwargs.update(large_tick_hits=large_ticks, small_tick_hits=0,
                          slider_end_hits=tails, n_geki=0, n_katu=0)
        achieved = _rosu.Performance(**kwargs).calculate(bm)
        # SS reference: rosu defaults an empty Performance to a perfect play
        maximum = _rosu.Performance(mods=int(mods), lazer=lazer).calculate(bm)
        return PerfBreakdown(
            aim_pct=component_pct(achieved.pp_aim, maximum.pp_aim),
            speed_pct=component_pct(achieved.pp_speed, maximum.pp_speed),
            acc_pct=component_pct(achieved.pp_accuracy, maximum.pp_accuracy),
            achieved_pp=float(achieved.pp),
            max_pp=float(maximum.pp))
    except Exception as e:  # noqa: BLE001 — breakdown is garnish, never fatal
        print(f"WARNING: performance breakdown failed ({e})", file=sys.stderr)
        return None


def star_rating(osu_path, mods: int) -> float | None:
    """The map's star rating at these mods (rosu Difficulty.stars) for the
    results-screen star pill, or None fail-soft."""
    if _rosu is None:
        return None
    try:
        bm = _rosu.Beatmap(path=str(osu_path))
        return float(_rosu.Difficulty(mods=int(mods)).calculate(bm).stars)
    except Exception:  # noqa: BLE001
        return None
