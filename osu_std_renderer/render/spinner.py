"""Spinner visuals + hit-lighting math — RENDER_PLAN.md §3.3/§2.5 with the
lazer legacy-skin implementations as the behavioral reference (the scene
draws; everything here is pure and unit-tested without GL).

STYLE AUTO-DETECT (§3.3, skin.go): the skin ships `spinner-background` →
OLD style (`spinner-background` + `spinner-metre` + `spinner-circle`);
else any new-style sprite → NEW style (`spinner-glow/bottom/top/middle2/
middle`); else "none" → the procedural Argon-ish spinner. Both skin
styles share `spinner-approachcircle`, `spinner-clear`, `spinner-spin`,
`spinner-rpm` — each falls back per-element.

ROTATION (SpinnerTrack): the visual rotation tracks the replay cursor
continuously — per-frame signed angle deltas around the spinner centre
(256,192), accumulated ONLY while a key is held (matching the ruleset's
_evaluate_spinner accumulation, which sums |delta| for progress; the same
deltas drive both, so the metre agrees with the judgment). Between replay
frames the rotation is lerped. Without a replay (--no-replay perfect
play) the track auto-spins at the reference constant RPMS = 0.00795
rev/ms (§2.5) = 477 RPM, the stable auto-spin/display cap.

Reference behaviors ported from lazer's legacy skins
(osu.Game.Rulesets.Osu/Skinning/Legacy/):
  LegacySpinner        SPRITE_SCALE = 0.625 — spinner textures are
                       authored in the 1024×768 UI space; ×0.625 converts
                       logical px to osu!px (666px circle → 416 osu!px).
                       We centre on the playfield centre; lazer's small
                       constant Y offsets (SPINNER_TOP_OFFSET family) are
                       SKIPPED — noted approximation.
  LegacyOldStyleSpinner   spinner-circle rotates with the accumulated
                       rotation; spinner-metre reveals bottom-up in 10%
                       BARS — getMetreHeight ports as metre_bar_count():
                       progress capped at 99 (the metre only ever shows
                       10/10 through the blink), the next bar BLINKS
                       (lazer flips a coin per frame; we use a
                       deterministic 30 ms square wave so renders are
                       reproducible). skin.ini SpinnerNoBlink=1 → steady.
                       Deviation: progress ≥ 1 pins a steady full metre
                       in BOTH modes (a cleared spinner should not sit at
                       9 bars under SpinnerNoBlink).
  LegacyNewStyleSpinner   spinner-top and spinner-middle2 rotate with the
                       full accumulated rotation, spinner-bottom at 1/3
                       of it; spinner-glow is additive, GLOW_BLUE
                       (3,151,255), alpha = spin progress; spinner-middle
                       fades white→red over the spinner's DURATION
                       (time-based, not progress).
  approach circle      1.9 → 0.1 linearly over [start, end] (§2.5).

RPM: lazer's SpinnerSpmCalculator semantics — rotation rate over a
trailing 595 ms window, in rev/min, displayed capped at 477.

HIT LIGHTING (§3.3): `lighting` (procedural: the soft radial `glow`
texture), combo-colour-tinted, additive, spawned at the popup position of
every non-miss object judgment, UNDER the judgment sprite. Lifecycle per
lazer's legacy hit lighting: fade 1→0 linearly over 400 ms while scaling
0.8→1.2 with a quint-out over 600 ms (the fade ends first).
"""
from __future__ import annotations

import bisect
import math

from ..beatmap.objects.spinner import RPMS
from ..replay.replay import KEY_K1, KEY_K2, KEY_M1, KEY_M2

SPINNER_CENTRE = (256.0, 192.0)     # osu!px (spinners ignore parsed coords)
SPRITE_SCALE = 0.625                # LegacySpinner: logical px → osu!px
GLOW_BLUE = (3 / 255.0, 151 / 255.0, 255 / 255.0)
RPM_DISPLAY_CAP = 477               # stable's display cap (= RPMS * 60000)
RPM_WINDOW_MS = 595.0               # SpinnerSpmCalculator record window
METRE_BLINK_PERIOD_MS = 30.0        # deterministic stand-in for per-frame RNG

APPROACH_START_SCALE = 1.9          # §2.5
APPROACH_END_SCALE = 0.1

SPIN_HOLD_MS = 500.0                # spinner-spin: solid this long past start
SPIN_FADE_MS = 300.0                # … then fades out
CLEAR_FADE_IN_MS = 120.0            # spinner-clear pop-in
CLEAR_POP_MS = 240.0                # 1.6→1 settle
SPIN_OFFSET_OSU = 130.0             # prompt offsets from the spinner centre
CLEAR_OFFSET_OSU = -105.0           # (osu!px; eyeballed against stable)

LIGHTING_FADE_MS = 400.0
LIGHTING_SCALE_MS = 600.0

# --- spinner BONUS popup (ArgonSpinner.bonusCounter) ---------------------------
# After the required spins, each extra full rotation awards a bonus tick and the
# Argon spinner pops a counter showing the CUMULATIVE bonus score. Ports of
# osu.Game.Rulesets.Osu/Objects/Drawables/DrawableSpinner.cs (score_per_tick,
# CurrentBonusScore, updateBonusScore) + osu.Game.Rulesets.Osu/Skinning/Argon/
# ArgonSpinner.cs (the bonusCounter transforms) + Spinner.cs (spin thresholds).
SPINNER_BONUS_SCORE_PER_TICK = 50   # OsuScoreProcessor base score for a
                                    # SpinnerBonusTick (HitResult.LargeBonus)
BONUS_SPINS_GAP = 2                 # Spinner.bonus_spins_gap
BONUS_DURATION_ERROR = 0.0001       # Spinner.ApplyDefaultsToSelf duration_error
BONUS_LIFE_MS = 1500.0             # non-MAX FadeOutFromOne(1500)
BONUS_MAX_LIFE_MS = 500.0          # MAX FadeOutFromOne(500)
BONUS_SCALE_MS = 1000.0           # ScaleTo(target, 1000, OutQuint)
BONUS_MAX_SCALE = 2.8             # MAX ScaleTo(2.8); non-MAX settles to 1.0
BONUS_POP_SCALE = 1.5            # ScaleTo(1.5) instant kick before the settle

_HELD_MASK = KEY_K1 | KEY_K2 | KEY_M1 | KEY_M2

OLD_STYLE_ELEMENTS = ("spinner-background", "spinner-metre", "spinner-circle")
NEW_STYLE_ELEMENTS = ("spinner-glow", "spinner-bottom", "spinner-top",
                      "spinner-middle2", "spinner-middle")
COMMON_ELEMENTS = ("spinner-approachcircle", "spinner-clear", "spinner-spin",
                   "spinner-rpm")


def detect_spinner_style(loaded: set[str]) -> str:
    """§3.3 auto-detect over the LOADED element set: "old" | "new" | "none"
    (`spinner-background` present wins even when new-style sprites also
    exist — full skins commonly ship both)."""
    if "spinner-background" in loaded:
        return "old"
    if any(name in loaded for name in NEW_STYLE_ELEMENTS):
        return "new"
    return "none"


def required_rotations(spinner_ratio: float, duration_ms: float) -> float:
    """Full spins needed to clear: duration_s × SpinnerRatio (§2.4; the
    same formula the ruleset judges with)."""
    return duration_ms / 1000.0 * spinner_ratio


class SpinnerTrack:
    """Accumulated rotation of one spinner, sampled at any map time.

    times / signed / abs_rot are parallel arrays (radians, accumulated);
    sample() lerps between them. `required_rot` is the clear requirement
    in radians; progress(t) = abs_rot(t) / required_rot.
    """

    def __init__(self, start: float, end: float, times: list[float],
                 signed: list[float], abs_rot: list[float],
                 required_rot: float):
        self.start = start
        self.end = end
        self.times = times
        self.signed = signed
        self.abs_rot = abs_rot
        self.required_rot = required_rot
        self._clear: float | None = self._find_clear()

    # -- constructors ---------------------------------------------------------

    @classmethod
    def from_frames(cls, frames, start: float, end: float,
                    required_spins: float) -> "SpinnerTrack":
        """Replay-driven: per-frame signed deltas around the centre,
        accumulated while a key is held (wrap-safe atan2 difference —
        exactly the ruleset's accumulation, kept signed for the visual)."""
        cx, cy = SPINNER_CENTRE
        times = [start]
        signed = [0.0]
        abs_rot = [0.0]
        s = a = 0.0
        prev_angle: float | None = None
        prev_held = False
        for f in frames:
            if f.time_ms < start:
                continue
            if f.time_ms > end:
                break
            ang = math.atan2(f.y - cy, f.x - cx)
            held = bool(f.keys & _HELD_MASK)
            if prev_angle is not None and held and prev_held:
                d = math.atan2(math.sin(ang - prev_angle),
                               math.cos(ang - prev_angle))
                s += d
                a += abs(d)
            prev_angle = ang
            prev_held = held
            times.append(f.time_ms)
            signed.append(s)
            abs_rot.append(a)
        return cls(start, end, times, signed, abs_rot,
                   required_spins * 2.0 * math.pi)

    @classmethod
    def auto(cls, start: float, end: float,
             required_spins: float, rpms: float = RPMS) -> "SpinnerTrack":
        """--no-replay perfect play: constant-rate spin at the §2.5
        auto-spin constant (0.00795 rev/ms = 477 RPM). Linear, so the
        two-point series lerps exactly."""
        total = 2.0 * math.pi * rpms * max(end - start, 0.0)
        return cls(start, end, [start, end], [0.0, total], [0.0, total],
                   required_spins * 2.0 * math.pi)

    # -- sampling -------------------------------------------------------------

    def _lerp(self, arr: list[float], t: float) -> float:
        if t <= self.times[0]:
            return arr[0]
        if t >= self.times[-1]:
            return arr[-1]
        i = bisect.bisect_right(self.times, t)
        t0, t1 = self.times[i - 1], self.times[i]
        w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return arr[i - 1] + (arr[i] - arr[i - 1]) * w

    def rotation(self, t: float) -> float:
        """Signed accumulated rotation (radians) — what the sprites rotate
        by (tracks the cursor's direction)."""
        return self._lerp(self.signed, t)

    def progress(self, t: float) -> float:
        """Spin progress 0..1+ (unclamped) vs the clear requirement."""
        if self.required_rot <= 0.0:
            return 1.0
        return self._lerp(self.abs_rot, t) / self.required_rot

    def rpm(self, t: float, window_ms: float = RPM_WINDOW_MS) -> float:
        """Displayed RPM: rev/min over the trailing window, capped 477."""
        t = min(max(t, self.start), self.end)
        t0 = max(t - window_ms, self.start)
        dt = t - t0
        if dt <= 1e-9:
            return 0.0
        dr = self._lerp(self.abs_rot, t) - self._lerp(self.abs_rot, t0)
        rpm = dr / (2.0 * math.pi) / dt * 60000.0
        return min(rpm, float(RPM_DISPLAY_CAP))

    def clear_time(self) -> float | None:
        """Map time the requirement is met (spinner-clear pops), or None."""
        return self._clear

    def time_at_rotation(self, target_rad: float) -> float | None:
        """Map time the accumulated |rotation| first reaches target_rad
        (linear-interpolated between samples), or None if never reached."""
        if target_rad <= 0.0:
            return self.times[0]
        if self.abs_rot[-1] < target_rad:
            return None
        i = bisect.bisect_left(self.abs_rot, target_rad)
        if i == 0:
            return self.times[0]
        a0, a1 = self.abs_rot[i - 1], self.abs_rot[i]
        t0, t1 = self.times[i - 1], self.times[i]
        w = (target_rad - a0) / (a1 - a0) if a1 > a0 else 1.0
        return t0 + (t1 - t0) * w

    def _find_clear(self) -> float | None:
        if self.required_rot <= 0.0:
            return self.start
        return self.time_at_rotation(self.required_rot)


# --- lifecycle curves (pure) --------------------------------------------------

def spinner_approach_scale(t: float, start: float, end: float) -> float | None:
    """§2.5: 1.9 → 0.1 linearly over [start, end]; before the start the
    ring holds at 1.9 (it rides the spinner's fade-in), after the end
    it's gone."""
    if t > end:
        return None
    if t <= start:
        return APPROACH_START_SCALE
    w = (t - start) / max(end - start, 1e-9)
    return APPROACH_START_SCALE + (APPROACH_END_SCALE - APPROACH_START_SCALE) * w


def metre_bar_count(progress: float, no_blink: bool, t: float) -> int:
    """LegacyOldStyleSpinner.getMetreHeight in bars (0..10): progress
    capped at 99% → floor(p/10) full bars; the next bar blinks on a
    deterministic 30 ms square wave unless SpinnerNoBlink. progress ≥ 1
    pins a steady 10 (deviation noted in the module docstring)."""
    if progress >= 1.0:
        return 10
    bars = int(min(max(progress, 0.0) * 100.0, 99.0) // 10)
    if not no_blink and int(t // METRE_BLINK_PERIOD_MS) % 2 == 0:
        bars = min(bars + 1, 10)
    return bars


def spin_prompt_alpha(t: float, start: float) -> float:
    """spinner-spin multiplier over the spinner's own alpha: solid through
    the approach and SPIN_HOLD_MS past the start, then fades out."""
    if t <= start + SPIN_HOLD_MS:
        return 1.0
    p = (t - start - SPIN_HOLD_MS) / SPIN_FADE_MS
    return max(0.0, 1.0 - p)


def clear_alpha_scale(t: float, clear_t: float | None) -> tuple[float, float] | None:
    """(alpha, scale) of spinner-clear: pops 1.6→1 (quad-out) fading in
    over CLEAR_FADE_IN_MS once the requirement is met; None before/never.
    It then stays put — the spinner's overall fade-out retires it."""
    if clear_t is None or t < clear_t:
        return None
    age = t - clear_t
    alpha = min(age / CLEAR_FADE_IN_MS, 1.0)
    p = min(age / CLEAR_POP_MS, 1.0)
    scale = 1.6 - 0.6 * (1.0 - (1.0 - p) ** 2)
    return alpha, scale


def lighting_alpha_scale(age_ms: float) -> tuple[float, float] | None:
    """(alpha, scale) of one hit-lighting flash: linear fade over 400 ms,
    0.8→1.2 quint-out expand over 600 ms (lazer's legacy transform pair —
    the fade ends the sprite's life first). None outside [0, 400)."""
    if age_ms < 0.0 or age_ms >= LIGHTING_FADE_MS:
        return None
    alpha = 1.0 - age_ms / LIGHTING_FADE_MS
    q = min(age_ms / LIGHTING_SCALE_MS, 1.0)
    scale = 0.8 + 0.4 * (1.0 - (1.0 - q) ** 5)
    return alpha, scale


def wants_lighting(kind) -> bool:
    """Hit lighting spawns on 300/100/50 object judgments, never a miss."""
    return getattr(kind, "value", kind) in ("300", "100", "50")


# --- spinner bonus popup (pure) -----------------------------------------------

def spinner_bonus_thresholds(min_rps: float, max_rps: float,
                             duration_ms: float) -> tuple[int, int]:
    """(SpinsRequiredForBonus, MaximumBonusSpins) from the map's OD-derived
    spin rates — osu.Game.Rulesets.Osu/Objects/Spinner.cs ApplyDefaultsToSelf:

        SpinsRequired   = (int)(minRps * secondsDuration + duration_error)
        MaximumBonusSpins = max(0, (int)(maxRps*sec + err)
                                     - SpinsRequired - bonus_spins_gap)
        SpinsRequiredForBonus = SpinsRequired + bonus_spins_gap

    minRps/maxRps are DifficultyRange(OD, CLEAR/COMPLETE_RPM_RANGE)/60, which
    difficulty.py already exposes as lz_spinner_min_rps / lz_spinner_max_rps."""
    sec = max(duration_ms, 0.0) / 1000.0
    err = BONUS_DURATION_ERROR
    spins_required = int(min_rps * sec + err)
    max_bonus = max(0, int(max_rps * sec + err) - spins_required
                    - BONUS_SPINS_GAP)
    return spins_required + BONUS_SPINS_GAP, max_bonus


def bonus_spin_events(track: "SpinnerTrack", spins_required_for_bonus: int,
                      max_bonus_spins: int) -> list[tuple[float, int, bool]]:
    """(time_ms, bonus_value, is_max) for each completed BONUS spin.

    DrawableSpinner.updateBonusScore increments completedFullSpins =
    floor(TotalRotation/360) on every full rotation; ArgonSpinner's
    bonusCounter pops on each change whose CurrentBonusScore > 0 (guard),
    where CurrentBonusScore = SCORE_PER_TICK · clamp(spins −
    SpinsRequiredForBonus, 0, MaximumBonusSpins). Once the cap is reached the
    counter re-pops "MAX" (is_max) on every further spin."""
    if max_bonus_spins <= 0:
        return []           # no bonus ticks → CurrentBonusScore stays 0
    two_pi = 2.0 * math.pi
    total_spins = int(track.abs_rot[-1] // two_pi)
    events: list[tuple[float, int, bool]] = []
    for k in range(spins_required_for_bonus + 1, total_spins + 1):
        tcross = track.time_at_rotation(k * two_pi)
        if tcross is None:
            break
        over = k - spins_required_for_bonus
        value = SPINNER_BONUS_SCORE_PER_TICK * min(over, max_bonus_spins)
        events.append((tcross, value, over >= max_bonus_spins))
    return events


def bonus_popup_state(age_ms: float, is_max: bool) -> tuple[float, float] | None:
    """(alpha, scale) of the bonus counter at `age_ms` since its pop, or None
    once faded — ArgonSpinner.bonusCounter: ScaleTo(1.5).Then().ScaleTo(target,
    1000, OutQuint) with FadeOutFromOne(life). Non-MAX target 1.0 / life 1500;
    MAX target 2.8 / life 500."""
    life = BONUS_MAX_LIFE_MS if is_max else BONUS_LIFE_MS
    if age_ms < 0.0 or age_ms >= life:
        return None
    alpha = 1.0 - age_ms / life                      # FadeOutFromOne (linear)
    target = BONUS_MAX_SCALE if is_max else 1.0
    p = min(age_ms, BONUS_SCALE_MS) / BONUS_SCALE_MS
    scale = BONUS_POP_SCALE + (target - BONUS_POP_SCALE) * (1.0 - (1.0 - p) ** 5)
    return alpha, scale
