"""OsuModRelax auto-tap synthesis (RENDER_PLAN.md §5.5 — the "Relax/Autopilot
get synthesized clicks/aim" clause).

A Relax (RX, legacy mod bit 0x80) .osr carries the player's cursor motion but
NO key presses — under Relax osu! auto-taps for you
(osu.Game.Rulesets.Osu/Mods/OsuModRelax.cs, Update()). Every frame therefore
has keys == 0, so a press-edge driven judgment/HUD pipeline sees zero clicks:
the raw sim misses every object. reconcile_to_counts then snaps the COUNTS back
to the .osr's authoritative totals, but everything that is NOT reconciled is
wrong — max combo balloons (misses, all equal-quality, get dumped on the last
objects so combo never breaks until the end), the key overlay stays dead, and
slider-follow tracking / spinner spin-up never engage (no key is ever held).

synthesize_relax_frames() reproduces the presses OsuModRelax injects, writing
them into the frames' key bitmask. Feeding the SAME downstream pipeline a
normal replay uses (ruleset click routing + slider/spinner tracking, the HUD
key overlay, cursor-press effects) then makes every visual resolve correctly,
while reconcile still keeps the displayed counts exact.

Autopilot (AP, bit 0x2000) is the mirror mod: it auto-AIMS but the player still
taps, so an AP .osr's frames DO contain real key presses — AP needs no
synthesis and is deliberately not handled here (it renders through the normal
path unchanged).

Ported behaviour — OsuModRelax.Update / addAction (relax_leniency = 3):
  * HitCircle / slider head: `requiresHit` when the cursor hovers the circle
    (within CircleRadius) and HitWindows.CanBeHit(time - startTime). We land
    the synthesized press on the hover frame NEAREST startTime inside the
    scoring window [start-meh, start+meh], so it registers where a real relax
    tap would (near-zero delta → a clean hit-error meter, minimal reconcile).
  * Slider body: `requiresHold` while the cursor is in the follow area
    (SliderInputManager.IsMouseInFollowArea(expanded: true) == 2.4×radius).
  * Spinner: `requiresHold` across its whole [start, end] span.
  * Buttons alternate K1/K2 on each fresh press (the wasLeft toggle), exactly
    as OsuModRelax.addAction picks `wasLeft ? Left : Right`.
"""
from __future__ import annotations

import bisect

from ..beatmap.objects import Slider, Spinner
from .replay import KEY_K1, KEY_K2, StdFrame

# osu.Game.Rulesets.Osu/Mods/OsuModRelax.cs
RELAX_LENIENCY = 3.0
# DrawableSliderBall.FOLLOW_AREA (expanded follow radius multiplier)
FOLLOW_AREA = 2.4
# legacy .osr Mods bit for Relax (osu! wiki)
MOD_RELAX = 0x80


def is_relax_meta(meta) -> bool:
    """True when the replay was played with Relax (RX). Reads the legacy mod
    bitmask (set for stable AND lazer, since RX is legacy-convertible) plus
    the lazer acronym list as a belt-and-braces fallback."""
    if meta is None:
        return False
    if int(getattr(meta, "mods", 0)) & MOD_RELAX:
        return True
    return any(a.upper() == "RX" for a in getattr(meta, "lazer_mods", ()) or ())


def synthesize_relax_frames(frames, beatmap):
    """Return a copy of `frames` with OsuModRelax's auto-tap key presses
    filled in (see module docstring). Non-Relax callers must not call this —
    it unconditionally synthesizes. Empty input is returned unchanged."""
    if not frames:
        return frames
    # local import: ruleset imports from replay, so importing it at module
    # load would be a cycle.
    from ..ruleset.ruleset import OsuHitWindows

    diff = beatmap.diff
    radius = diff.get_radius()
    r2 = radius * radius
    follow_r2 = (radius * FOLLOW_AREA) ** 2
    meh = OsuHitWindows(diff.od).meh
    preempt = diff.preempt

    objs = sorted(beatmap.hit_objects, key=lambda o: o.get_start_time())
    n = len(objs)
    kinds: list[str] = []
    starts: list[float] = []
    ends: list[float] = []
    heads: list[tuple[float, float]] = []
    for o in objs:
        if isinstance(o, Spinner):
            kinds.append("spinner")
        elif isinstance(o, Slider):
            kinds.append("slider")
        else:
            kinds.append("circle")
        starts.append(o.get_start_time())
        ends.append(o.get_end_time())
        heads.append(o.get_stacked_start_position(diff))

    frame_times = [f.time_ms for f in frames]

    # ---- pass A: choose the tap frame per clickable object ---------------------
    # the hover frame nearest startTime inside the scoring window; None = the
    # cursor is never over the object in-window → a genuine (aim) miss, exactly
    # as under real Relax.
    tap_frame: list[int | None] = [None] * n
    for i in range(n):
        if kinds[i] == "spinner":
            continue
        st = starts[i]
        ox, oy = heads[i]
        j = bisect.bisect_left(frame_times, st - meh)
        best: int | None = None
        best_d: float | None = None
        while j < len(frames):
            f = frames[j]
            if f.time_ms > st + meh:
                break
            dx = f.x - ox
            dy = f.y - oy
            if dx * dx + dy * dy <= r2:
                d = abs(f.time_ms - st)
                if best_d is None or d < best_d:
                    best_d = d
                    best = j
            j += 1
        tap_frame[i] = best

    taps_at: dict[int, list[int]] = {}
    for i, fi in enumerate(tap_frame):
        if fi is not None:
            taps_at.setdefault(fi, []).append(i)

    # ---- pass B: forward walk, emit the held key bitmask per frame --------------
    out: list[StdFrame] = []
    was_left = True        # OsuModRelax starts on the left button
    is_down = False
    active = 0             # currently-held channel bit
    lo = 0                 # sliding window start over objects (by end time)
    for fi, f in enumerate(frames):
        t = f.time_ms
        cx, cy = f.x, f.y
        while lo < n and ends[lo] + meh < t:
            lo += 1

        requires_hit = fi in taps_at
        requires_hold = False
        k = lo
        while k < n:
            st = starts[k]
            if t < st - preempt - RELAX_LENIENCY:
                break          # objects are start-ordered — none later is live
            if kinds[k] == "spinner":
                if st <= t <= ends[k]:
                    requires_hold = True
            elif kinds[k] == "slider":
                if st <= t <= ends[k]:
                    bx, by = objs[k].get_stacked_position_at(t, diff)
                    ddx = cx - bx
                    ddy = cy - by
                    if ddx * ddx + ddy * ddy <= follow_r2:
                        requires_hold = True
            k += 1

        # OsuModRelax.addAction: requiresHit pulses a fresh press (release+press
        # → a new edge even mid-hold), otherwise the button is held iff
        # requiresHold; on each fresh press the button alternates.
        if requires_hit:
            active = KEY_K1 if was_left else KEY_K2
            was_left = not was_left
            is_down = True
            keys = active
        elif requires_hold:
            if not is_down:
                active = KEY_K1 if was_left else KEY_K2
                was_left = not was_left
                is_down = True
            keys = active
        else:
            is_down = False
            keys = 0

        out.append(StdFrame(time_ms=t, x=f.x, y=f.y, keys=keys))
    return out
