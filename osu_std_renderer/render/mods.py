"""Mod-driven playfield visuals — OsuModHidden fades + OsuModFlashlight overlay.

Ported from ppy/osu (MIT), cited per function:

  osu.Game.Rulesets.Osu/Mods/OsuModHidden.cs
      FADE_IN_DURATION_MULTIPLIER  = 0.4   (fade-in is the object's TimeFadeIn)
      FADE_OUT_DURATION_MULTIPLIER = 0.3
      applyState()/getFadeOutParameters(): a hit circle's fade-out starts at
      StartTime - TimePreempt + TimeFadeIn and lasts TimePreempt*0.3 (linear,
      Easing.None); a slider's Body fades over Duration + TimePreempt*0.3 with
      Easing.Out; OsuModHidden : IHidesApproachCircles hides every approach
      circle.

  osu.Game.Rulesets.Osu/Mods/OsuModFlashlight.cs
      DefaultFlashlightSize = 180 (osu!px)
      GetSizeFor(combo): combo>200 → 0.8×, combo>100 → 0.9×, else 1.0×
      OnComboChange transforms FlashlightSize over FLASHLIGHT_FADE_DURATION.
  osu.Game/Rulesets/Mods/ModFlashlight.cs
      FLASHLIGHT_FADE_DURATION = 800 (ms)
  osu.Game/Rulesets/Mods/Flashlight.cs
      the cursor-following dark overlay with a soft circular cutout.
"""
from __future__ import annotations

MOD_HIDDEN = 1 << 3
MOD_FLASHLIGHT = 1 << 10


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


# ----------------------------------------------------------------------------
# OsuModHidden — fade-out multipliers (multiply the object's normal alpha)
# ----------------------------------------------------------------------------
HIDDEN_FADE_OUT_MULT = 0.3          # OsuModHidden.FADE_OUT_DURATION_MULTIPLIER
HIDDEN_FADE_IN_MULT  = 0.4          # OsuModHidden.FADE_IN_DURATION_MULTIPLIER


def hidden_circle_fade(t: float, start: float, preempt: float,
                       time_fade_in: float) -> float:
    """Hit-circle (and slider-head / combo number) OsuModHidden fade-out
    factor in [0,1]. The object fades in normally over TimeFadeIn, then this
    linear fade-out (Easing.None) runs from StartTime-Preempt+TimeFadeIn over
    Preempt*0.3 — OsuModHidden.getFadeOutParameters default branch +
    `circle.FadeOut(fadeDuration)`."""
    fade_start = start - preempt + time_fade_in
    dur = preempt * HIDDEN_FADE_OUT_MULT
    if t <= fade_start:
        return 1.0
    if dur <= 0.0:
        return 0.0
    return _clamp01(1.0 - (t - fade_start) / dur)


def hidden_slider_fade(t: float, start: float, end: float, preempt: float,
                       time_fade_in: float) -> float:
    """Slider-body OsuModHidden fade-out factor in [0,1]. Starts at
    StartTime-Preempt+TimeFadeIn and lasts slider.Duration + Preempt*0.3 with
    Easing.Out (osu!framework Easing.Out == OutQuad) — OsuModHidden slider
    branch `slider.Body.FadeOut(fadeDuration, Easing.Out)` with
    fadeDuration = slider.Duration + fadeOutDuration."""
    fade_start = start - preempt + time_fade_in
    dur = (end - start) + preempt * HIDDEN_FADE_OUT_MULT
    if t <= fade_start:
        return 1.0
    if dur <= 0.0:
        return 0.0
    p = _clamp01((t - fade_start) / dur)
    # FadeOut(Easing.Out): alpha interpolates 1→0 with OutQuad progress
    # eased = p*(2-p); alpha = 1-eased = (1-p)^2.
    return (1.0 - p) * (1.0 - p)


# ----------------------------------------------------------------------------
# OsuModFlashlight — cursor-following dark overlay with a shrinking cutout
# ----------------------------------------------------------------------------
FLASHLIGHT_DEFAULT_SIZE = 180.0     # OsuModFlashlight.DefaultFlashlightSize (osu!px)
FLASHLIGHT_FADE_DURATION = 800.0    # ModFlashlight.FLASHLIGHT_FADE_DURATION (ms)


def flashlight_size_for(combo: int) -> float:
    """OsuModFlashlight.GetSizeFor(combo): DefaultFlashlightSize scaled by the
    combo breakpoints (>200 → 0.8×, >100 → 0.9×, else 1×)."""
    if combo > 200:
        return FLASHLIGHT_DEFAULT_SIZE * 0.8
    if combo > 100:
        return FLASHLIGHT_DEFAULT_SIZE * 0.9
    return FLASHLIGHT_DEFAULT_SIZE


def build_flashlight_timeline(events) -> list[tuple[float, float, float]]:
    """From the judgment stream build the size-change schedule as
    [(change_time_ms, from_size_osu, to_size_osu)]. FlashlightSize is driven
    by combo via OnComboChange, so a change point is emitted whenever
    GetSizeFor(combo_after) differs from the running size."""
    changes: list[tuple[float, float, float]] = []
    cur = FLASHLIGHT_DEFAULT_SIZE
    for ev in sorted(events, key=lambda e: e.time_ms):
        combo = getattr(ev, "combo_after", None)
        if combo is None:
            continue
        size = flashlight_size_for(combo)
        if size != cur:
            changes.append((ev.time_ms, cur, size))
            cur = size
    return changes


def flashlight_size_at(timeline: list[tuple[float, float, float]],
                       t: float) -> float:
    """FlashlightSize (osu!px) at time t — the 800 ms OnComboChange transform
    lerps between the two sizes at each change point."""
    size = FLASHLIGHT_DEFAULT_SIZE
    for change_t, from_s, to_s in timeline:
        if t < change_t:
            break
        if t < change_t + FLASHLIGHT_FADE_DURATION:
            p = (t - change_t) / FLASHLIGHT_FADE_DURATION
            return from_s + (to_s - from_s) * p
        size = to_s
    return size
