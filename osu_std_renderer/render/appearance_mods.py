"""Approach/circle-appearance mods — FR (Freeze Frame), AD (Approach Different),
TC (Traceable). The pure-math core (approach-circle scale curves + FR's per-
object preempt); the scene (render/scene.py) wires each config to its acronym
and draws it.

All three are purely VISUAL (``ModWithVisibilityAdjustment`` / draw-only): they
change WHEN objects appear (FR) or HOW the approach circle scales (FR/AD) or
WHAT of the hit circle is drawn (TC). Object geometry, the replay cursor and
the judgement/reconcile are UNTOUCHED — the renderer never mutates the beatmap
difficulty (``diff.preempt`` stays the ruleset's value), so the SimResult is
byte-identical with or without these mods.

Ported from ppy/osu (MIT), cited per mod:

  osu.Game.Rulesets.Osu/Mods/OsuModFreezeFrame.cs  (acronym "FR", ModType.Fun)
      IApplicableToBeatmap: ``originalPreempt = firstHitObject.TimePreempt``;
      walking objects in start order, ``lastNewComboTime`` tracks the last
      NewCombo object's StartTime, and every non-Spinner object gets
      ``TimePreempt += StartTime - lastNewComboTime`` — so a whole combo's
      objects share one appear time (the combo's first-object appear time) and
      stay "frozen"/burned in until their hit. TimeFadeIn is NOT changed.
      IApplicableToDrawableHitObject: each hit circle's approach circle is
      ``ScaleTo(4 * TimePreempt/originalPreempt)`` then, from
      ``StartTime - TimePreempt``, ``ScaleTo(1, TimePreempt)`` (Easing.None,
      i.e. linear) and reaches 1 at the hit. The rescale ("ensuring the AR
      isn't changed due to the new preempt") keeps the ring an approximately
      normal size at the object's original appear time despite the longer
      preempt — it starts larger and earlier; the ring is NOT exactly 4 at the
      original appear time and the shrink rate is not literally preserved.

  osu.Game.Rulesets.Osu/Mods/OsuModApproachDifferent.cs  (acronym "AD")
      ``Scale`` BindableFloat(4){Min 1.5, Max 10} sets the approach circle's
      initial size; the update lambda does ``ApproachCircle.ScaleTo(Scale)``
      then ``ScaleTo(1, TimePreempt, EASING[Style])`` — the ring scales
      Scale→1 over TimePreempt with the per-style easing below. Only the
      approach circle changes; TimePreempt, TimeFadeIn, alpha and the circle
      body are all normal.

  osu.Game.Rulesets.Osu/Mods/OsuModTraceable.cs  (acronym "TC")
      applyTraceableState hides each hit circle's whole CirclePiece ("we only
      want to see the approach circle"), hides the slider tail, and makes the
      slider body outline-only (AccentColour opacity 0, BorderColour = accent).
      Handled in the scene as a draw gate (no math here).
"""
from __future__ import annotations

import math

# The approach circle's default initial scale (§2.5 / lazer's ``4 *``).
APPROACH_START_SCALE = 4.0


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# --- osu!framework easings (DefaultEasingFunction.ApplyEasing) ----------------
# Only the ten the AnimationStyle enum maps to. Each takes/returns [0,1] except
# InBack, which dips slightly below 0 near the start (the "gravity" pull-back
# that makes the approach ring bulge a touch larger before shrinking).
_BACK_CONST = 1.70158


def ease_none(p: float) -> float:
    """Easing.None — linear."""
    return _clamp01(p)


def ease_in_back(p: float) -> float:
    """Easing.InBack: p²·((s+1)·p − s), s = 1.70158 (dips below 0 early)."""
    p = _clamp01(p)
    return p * p * ((_BACK_CONST + 1.0) * p - _BACK_CONST)


def ease_in_quad(p: float) -> float:
    """Easing.In / Easing.InQuad."""
    p = _clamp01(p)
    return p * p


def ease_in_cubic(p: float) -> float:
    """Easing.InCubic."""
    p = _clamp01(p)
    return p * p * p


def ease_in_quint(p: float) -> float:
    """Easing.InQuint."""
    p = _clamp01(p)
    return p ** 5


def ease_out_quad(p: float) -> float:
    """Easing.Out / Easing.OutQuad: p·(2 − p)."""
    p = _clamp01(p)
    return p * (2.0 - p)


def ease_out_cubic(p: float) -> float:
    """Easing.OutCubic: 1 − (1 − p)³."""
    p = _clamp01(p)
    return 1.0 - (1.0 - p) ** 3


def ease_out_quint(p: float) -> float:
    """Easing.OutQuint: 1 − (1 − p)⁵."""
    p = _clamp01(p)
    return 1.0 - (1.0 - p) ** 5


def ease_in_out_cubic(p: float) -> float:
    """Easing.InOutCubic."""
    p = _clamp01(p) * 2.0
    if p < 1.0:
        return 0.5 * p * p * p
    p -= 2.0
    return 0.5 * (p * p * p + 2.0)


def ease_in_out_quint(p: float) -> float:
    """Easing.InOutQuint."""
    p = _clamp01(p) * 2.0
    if p < 1.0:
        return 0.5 * p ** 5
    p -= 2.0
    return 0.5 * (p ** 5 + 2.0)


# AnimationStyle enum member (lower-case) -> osu!framework easing.
# OsuModApproachDifferent.cs switch: Linear→None, Gravity→InBack,
# InOut1→InOutCubic, InOut2→InOutQuint, Accelerate1→In, Accelerate2→InCubic,
# Accelerate3→InQuint, Decelerate1→Out, Decelerate2→OutCubic, Decelerate3→OutQuint.
AD_EASINGS = {
    "linear": ease_none,
    "gravity": ease_in_back,
    "inout1": ease_in_out_cubic,
    "inout2": ease_in_out_quint,
    "accelerate1": ease_in_quad,
    "accelerate2": ease_in_cubic,
    "accelerate3": ease_in_quint,
    "decelerate1": ease_out_quad,
    "decelerate2": ease_out_cubic,
    "decelerate3": ease_out_quint,
}


# --- AD: Approach Different ----------------------------------------------------
def approach_different_scale(t: float, start_time: float, preempt: float,
                             scale: float, style: str) -> float | None:
    """Approach-circle scale at ``t`` under Approach Different, or None outside
    its life ([start − preempt, start); it vanishes at the hit).

    ``ScaleTo(scale)`` then ``ScaleTo(1, preempt, EASING[style])``: the value
    interpolates scale→1 as ``scale + (1 − scale)·EASING(progress)`` over the
    preempt window (progress 0 at appear, 1 at the hit). With the default
    Gravity (InBack) the eased value dips below 0 early, so the ring first
    bulges slightly ABOVE ``scale`` before shrinking."""
    if preempt <= 0.0 or t >= start_time or t < start_time - preempt:
        return None
    ease = AD_EASINGS.get(style, ease_in_back)   # unknown -> default Gravity
    p = (t - (start_time - preempt)) / preempt
    return scale + (1.0 - scale) * ease(p)


# --- FR: Freeze Frame ----------------------------------------------------------
def freeze_preempt(orig_preempt: float, start_time: float,
                   combo_start_time: float) -> float:
    """A non-Spinner object's extended TimePreempt under Freeze Frame:
    ``originalPreempt + (StartTime − lastNewComboTime)``. The first object of a
    combo (StartTime == combo_start_time) keeps ``orig_preempt``; later objects
    get a longer preempt so they appear back at the combo's start."""
    return orig_preempt + (start_time - combo_start_time)


def freeze_frame_scale(t: float, start_time: float, preempt: float,
                       orig_preempt: float) -> float | None:
    """Approach-circle scale at ``t`` under Freeze Frame, or None outside its
    life ([start − preempt, start), with ``preempt`` the object's EXTENDED
    TimePreempt from :func:`freeze_preempt`).

    Initial scale ``4 * preempt/orig_preempt`` shrinking LINEARLY (Easing.None)
    to 1 over the extended preempt — ppy/osu's rescale that keeps the ring an
    approximately normal size at the object's original appear time despite the
    longer preempt. (At that appear time the ring is ``5 − orig_preempt/preempt``,
    NOT exactly 4, and the shrink rate is not literally preserved.)"""
    if orig_preempt <= 0.0 or preempt <= 0.0 \
            or t >= start_time or t < start_time - preempt:
        return None
    init = APPROACH_START_SCALE * (preempt / orig_preempt)
    p = (t - (start_time - preempt)) / preempt
    return init + (1.0 - init) * _clamp01(p)


def freeze_preempts(objects, orig_preempt: float, is_spinner) -> dict:
    """Precompute ``id(obj) -> extended preempt`` for every non-Spinner object
    under Freeze Frame. ``objects`` must be in non-decreasing start-time order
    (the scene's ``self.objects``); ``is_spinner(obj)`` gates the Spinner
    exemption. ``obj`` needs ``get_start_time()`` and a truthy ``new_combo``
    attribute on each combo's first object (the parser's post-numbering flag).

    Every object in a combo resolves to the SAME appear time
    (comboStart − orig_preempt), which is why the whole combo's approach
    circles pop in together — the Freeze Frame effect."""
    out: dict = {}
    last_nc: float | None = None
    for o in objects:
        st = o.get_start_time()
        if last_nc is None or getattr(o, "new_combo", False):
            last_nc = st
        if not is_spinner(o):
            out[id(o)] = freeze_preempt(orig_preempt, st, last_nc)
    return out
