"""HP drain + gain — an offline port of lazer's CURRENT default osu!
health model (MIT, ppy/osu master; class + file cited per function):

    osu.Game/Rulesets/Scoring/DrainingHealthProcessor.cs
        ComputeDrainRate  (binary-search drain targeting the HP-mapped
                           minimum health of a perfect play)
        Update            (continuous drain between DrainStartTime and the
                           last object, paused across break periods)
    osu.Game.Rulesets.Osu/Scoring/OsuHealthProcessor.cs
        GetHealthIncreaseFor  (per-result increase table + the stable
                               combo-end bonus +0.07/+0.05/+0.03)

lazer wires exactly this pair to its HUD health bar for osu! (the LEGACY
`LegacyDrainingHealthProcessor` only runs under the Classic mod), so this
is the drain model the ArgonHealthDisplay port consumes.

The processor is replayed OFFLINE into a piecewise-linear timeline
(breakpoints + hp_at bisect) so the HUD stays stateless per frame:

  * drain starts at the FIRST OBJECT's start time (lazer Player passes
    `playableBeatmap.HitObjects[0].StartTime` as drainStartTime) and ends
    at the last object's end time;
  * no drain across break periods — the no-drain window spans from the
    last object end BEFORE the break to the first object start AFTER it
    (DrainingHealthProcessor's PeriodTracker construction);
  * gains/losses land at the REAL judgment moments from the ruleset sim
    (hit_time / window close / tick times / slider end), amounts from the
    OsuHealthProcessor table;
  * health clamps to [0, 1]; reaching 0 does NOT fail the run (the replay
    demonstrably finished — honest simplification, logged by the caller).

HONEST APPROXIMATIONS (all small, all display-only):
  * spinner bonus ticks (SmallBonus/LargeBonus 0.0085/0.01 per spin) are
    not simulated — bonus results are EXCLUDED from drain computation in
    lazer too, so only tiny in-spinner gains are missed;
  * the combo-end bonus applies at the object's final part time with the
    per-combo Perfect/Good/None downgrade tracked from our part outcomes;
  * stable replays reuse the same lazer-default model (heads as timing
    judgments); the Classic-mod legacy processor is not ported.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

from ..beatmap.objects import Slider, Spinner
from .ruleset import JudgmentKind, _difficulty_range

# DrainingHealthProcessor: minimum health a perfect play reaches, by HP
MIN_HEALTH_TARGETS = (0.99, 0.90, 0.40)
MINIMUM_HEALTH_ERROR = 0.01

# OsuHealthProcessor.getHealthIncreaseFor
GAIN_GREAT = 0.03
GAIN_OK = 0.011
GAIN_MEH = 0.002
GAIN_TICK = 0.015           # SliderTick LargeTickHit
GAIN_REPEAT = 0.02          # SliderRepeat
GAIN_TAIL = 0.02            # SliderTailHit / SliderTailCircle
MISS_RANGE = (-0.03, -0.125, -0.2)         # HitResult.Miss
TICK_MISS_RANGE = (-0.02, -0.075, -0.14)   # Small/LargeTickMiss

# OsuHealthProcessor combo-end bonus (ComboResult Perfect/Good/None)
COMBO_BONUS = {"perfect": 0.07, "good": 0.05, "none": 0.03}

_TIER_GAIN = {
    JudgmentKind.HIT300: GAIN_GREAT,
    JudgmentKind.HIT100: GAIN_OK,
    JudgmentKind.HIT50: GAIN_MEH,
}


@dataclass(frozen=True)
class HealthEvent:
    """One discrete health change (drain is continuous, not an event)."""
    time_ms: float
    amount: float
    is_hit: bool


def compute_drain_rate(increases: list[tuple[float, float]],
                       breaks: list[tuple[float, float]],
                       drain_start: float, target_min: float,
                       ) -> float:
    """DrainingHealthProcessor.ComputeDrainRate: binary-search the drain
    rate (hp/ms) so a PERFECT play's lowest health lands within
    MINIMUM_HEALTH_ERROR of target_min. `increases` = (time, amount) of
    every non-bonus judgment at max result; `breaks` = the no-drain
    periods (already object-snapped)."""
    if len(increases) <= 1:
        return 0.0

    adjustment = 1
    result = 1.0

    while adjustment > 0:                     # overflow guard, as upstream
        current_health = 1.0
        lowest_health = 1.0
        current_break = 0

        for i, (current_time, amount) in enumerate(increases):
            last_time = increases[i - 1][0] if i > 0 else drain_start

            while (current_break < len(breaks)
                   and breaks[current_break][1] <= current_time):
                # no drain for the full period between the two objects
                last_time = current_time
                current_break += 1

            current_health -= (current_time - last_time) * result
            lowest_health = min(lowest_health, current_health)
            current_health = min(1.0, current_health + amount)

            if lowest_health < 0:
                break

        if abs(lowest_health - target_min) <= MINIMUM_HEALTH_ERROR:
            break

        adjustment *= 2
        if adjustment > (1 << 30):
            # upstream's guard is Int32 overflow (adjustment goes
            # negative and the while exits); Python ints never overflow,
            # so cap at the same magnitude
            break
        result += (1.0 / adjustment
                   * (1 if lowest_health > target_min else -1))

    return max(result, 0.0)


def _no_drain_periods(beatmap) -> list[tuple[float, float]]:
    """DrainingHealthProcessor.ApplyBeatmap's PeriodTracker: each break
    expands to [last object end before it, first object start after it]."""
    objs = sorted(beatmap.hit_objects, key=lambda o: o.get_start_time())
    starts = [o.get_start_time() for o in objs]
    ends = [o.get_end_time() for o in objs]
    periods = []
    for p in beatmap.pauses:
        before = [e for e in ends if e <= p.start_time]
        after = [s for s in starts if s >= p.end_time]
        periods.append((before[-1] if before else -1e18,
                        after[0] if after else 1e18))
    return periods


class HealthTimeline:
    """The offline health processor: perfect-play drain-rate solve, then
    the REAL judgment stream replayed into (time, hp) breakpoints.

    hp_at(t)      piecewise-linear health, stateless (bisect)
    miss_events   [(time, hp_just_before)] for every health-LOSING result
                  (drives the ArgonHealthDisplay miss animation)
    gain_events   [time...] of every hit result (drives Flash/Bulge)
    drain_rate    hp per ms (the solved rate)
    """

    def __init__(self, beatmap, sim):
        self.hp = beatmap.diff.hp
        objs = sorted(beatmap.hit_objects, key=lambda o: o.get_start_time())
        self.drain_start = objs[0].get_start_time() if objs else 0.0
        self.gameplay_end = max((o.get_end_time() for o in objs),
                                default=0.0)
        self.breaks = _no_drain_periods(beatmap)
        self.target_min = _difficulty_range(self.hp, *MIN_HEALTH_TARGETS)
        self.miss_loss = _difficulty_range(self.hp, *MISS_RANGE)
        self.tick_miss_loss = _difficulty_range(self.hp, *TICK_MISS_RANGE)

        perfect = self._perfect_increases(objs, sim)
        self.drain_rate = compute_drain_rate(
            perfect, self.breaks, self.drain_start, self.target_min)

        events = self._real_events(objs, sim)
        self._build_breakpoints(events)

    # ---- perfect-play increases (drain-rate input) --------------------------------

    def _perfect_increases(self, objs, sim) -> list[tuple[float, float]]:
        """Every non-bonus judgment at its MAX result: what lazer's
        simulated autoplay feeds ComputeDrainRate."""
        out: list[tuple[float, float]] = []
        for obj in objs:
            start, end = obj.get_start_time(), obj.get_end_time()
            if isinstance(obj, Spinner):
                out.append((end, GAIN_GREAT))
            elif isinstance(obj, Slider):
                out.append((start, GAIN_GREAT))          # head (timing max)
                for tp in obj.score_points:
                    if tp.kind == "tick":
                        out.append((tp.time, GAIN_TICK))
                    elif tp.kind == "reverse":
                        out.append((tp.time, GAIN_REPEAT))
                    else:                                # legacy last tick
                        out.append((end, GAIN_TAIL))
            else:
                out.append((start, GAIN_GREAT))
        out.sort(key=lambda e: e[0])
        return out

    # ---- real-play events -----------------------------------------------------------

    def _real_events(self, objs, sim) -> list[HealthEvent]:
        events: list[HealthEvent] = []
        combo_result = "perfect"                 # OsuHealthProcessor state

        def degrade(level: str) -> None:
            nonlocal combo_result
            order = ("none", "good", "perfect")
            if order.index(level) < order.index(combo_result):
                combo_result = level

        for i, obj in enumerate(objs):
            v = sim.verdicts.get(id(obj))
            if v is None:
                continue
            if getattr(obj, "new_combo", False):
                combo_result = "perfect"

            last_judged_at = v.end_time
            hit = v.kind is not JudgmentKind.MISS

            if v.obj_kind == "circle":
                t = v.hit_time if v.hit_time is not None else v.deadline
                last_judged_at = t
                if hit:
                    events.append(HealthEvent(t, _TIER_GAIN[v.kind], True))
                    if v.kind is JudgmentKind.HIT100:
                        degrade("good")
                    elif v.kind is JudgmentKind.HIT50:
                        degrade("none")
                else:
                    events.append(HealthEvent(t, self.miss_loss, False))
                    degrade("none")
            elif v.obj_kind == "spinner":
                if hit:
                    events.append(HealthEvent(
                        v.end_time, _TIER_GAIN[v.kind], True))
                    if v.kind is JudgmentKind.HIT100:
                        degrade("good")
                    elif v.kind is JudgmentKind.HIT50:
                        degrade("none")
                else:
                    events.append(HealthEvent(
                        v.end_time, self.miss_loss, False))
                    degrade("none")
            else:                                # slider parts
                for p in v.parts:
                    if p.kind == "head":
                        t = (v.hit_time if v.hit_time is not None
                             else v.deadline)
                        if p.hit:
                            # lazer: heads are timing judgments — gain by
                            # the head's tier when we know it, Great-ish
                            # for reconciled phantom hits
                            tier = _head_tier(v)
                            events.append(HealthEvent(t, tier, True))
                        else:
                            events.append(HealthEvent(
                                t, self.miss_loss, False))
                            degrade("none")
                    elif p.kind in ("tick", "repeat"):
                        amt = GAIN_TICK if p.kind == "tick" else GAIN_REPEAT
                        if p.hit:
                            events.append(HealthEvent(p.time, amt, True))
                        else:
                            events.append(HealthEvent(
                                p.time, self.tick_miss_loss, False))
                            degrade("good")      # LargeTickMiss → Good
                    else:                        # tail
                        last_judged_at = p.time
                        if p.hit:
                            events.append(HealthEvent(p.time, GAIN_TAIL,
                                                      True))
                        else:
                            # SliderTailCircle miss: no health change,
                            # combo downgraded to Good (upstream special)
                            degrade("good")

            # combo-end bonus at the object's last judgment moment
            last_in_combo = (i + 1 >= len(objs)
                             or getattr(objs[i + 1], "new_combo", False))
            if last_in_combo and hit:
                events.append(HealthEvent(
                    last_judged_at, COMBO_BONUS[combo_result], True))

        events.sort(key=lambda e: e.time_ms)
        return events

    # ---- timeline -------------------------------------------------------------------

    def _drained(self, t0: float, t1: float) -> float:
        """Drain accumulated over [t0, t1] — clamped to the gameplay
        window, zero inside break periods."""
        a = max(t0, self.drain_start)
        b = min(t1, self.gameplay_end)
        if b <= a:
            return 0.0
        span = b - a
        for bs, be in self.breaks:
            lo, hi = max(a, bs), min(b, be)
            if hi > lo:
                span -= hi - lo
        return span * self.drain_rate

    def _build_breakpoints(self, events: list[HealthEvent]) -> None:
        # breakpoint lattice: every event time + break edges + window edges
        cuts = sorted({self.drain_start, self.gameplay_end,
                       *(t for p in self.breaks for t in p
                         if -1e17 < t < 1e17),
                       *(e.time_ms for e in events)})
        self.bp_times: list[float] = []
        self.bp_values: list[float] = []
        self.miss_events: list[tuple[float, float]] = []
        self.gain_events: list[float] = []

        hp = 1.0
        prev = self.drain_start
        ev_i = 0
        self.bp_times.append(prev)
        self.bp_values.append(hp)
        for t in cuts:
            if t < prev:
                continue
            hp = max(0.0, hp - self._drained(prev, t))
            prev = t
            self.bp_times.append(t)
            self.bp_values.append(hp)
            while ev_i < len(events) and events[ev_i].time_ms <= t:
                e = events[ev_i]
                ev_i += 1
                if e.amount < 0:
                    self.miss_events.append((e.time_ms, hp))
                elif e.is_hit:
                    self.gain_events.append(e.time_ms)
                hp = min(1.0, max(0.0, hp + e.amount))
                self.bp_times.append(t)
                self.bp_values.append(hp)
        self.final_hp = hp

    def hp_at(self, t: float) -> float:
        times, vals = self.bp_times, self.bp_values
        if not times or t <= times[0]:
            return 1.0
        if t >= times[-1]:
            # past the last breakpoint: drain may still run to gameplay end
            return max(0.0, vals[-1] - self._drained(times[-1], t))
        i = bisect.bisect_right(times, t)
        t0, t1 = times[i - 1], times[i]
        v0, v1 = vals[i - 1], vals[i]
        if t1 <= t0:
            return v1
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    def last_miss_at(self, t: float) -> tuple[float, float] | None:
        """(time, hp_before) of the last health-losing result at/before t."""
        i = bisect.bisect_right([m[0] for m in self.miss_events], t) - 1
        return self.miss_events[i] if i >= 0 else None

    def last_gain_at(self, t: float) -> float | None:
        i = bisect.bisect_right(self.gain_events, t) - 1
        return self.gain_events[i] if i >= 0 else None


def _head_tier(v) -> float:
    """Gain for a hit slider head: its timing tier when the sim kept one
    (lazer semantics — the head IS the counted judgment)."""
    if v.delta is None:
        return GAIN_GREAT
    # the verdict's final kind is the head's tier for lazer sliders
    return _TIER_GAIN.get(v.kind, GAIN_GREAT)
