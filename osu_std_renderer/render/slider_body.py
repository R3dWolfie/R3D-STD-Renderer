"""Slider body renderer — STUB. This is the Phase-0 spike (APPROACH.md §4
"Spike the slider-body shader in moderngl (1–2 days)") and the single
genuinely-new graphics problem in the std engine (APPROACH.md §3.A item 1).

THE TECHNIQUE (what danser's sliderrenderer and lazer both do — the
distance-field / depth-trick tube):

  Geometry pass (offscreen FBO with a DEPTH buffer):
    * For each segment of the flattened path (curves/StdSliderPath.path),
      emit a quad extruded ±CircleRadius perpendicular to the segment,
      plus join geometry at vertices (or simply a screen-aligned quad per
      path point — the "stamped discs" variant; danser extrudes).
    * Fragment shader computes d = distance(fragment, segment) and writes
      gl_FragDepth = d / CircleRadius, discarding d > CircleRadius.
    * Depth test GL_LESS keeps, per pixel, the SMALLEST distance to the
      path — the union of swept circles with zero overdraw artifacts.
      (This is why a depth buffer, not blending, is mandatory: alpha
      blending would double-darken self-overlaps; aspire sliders overlap
      constantly.)

  Shade pass (fullscreen quad over the body FBO):
    * Sample the depth (=normalized distance) texture; shade by distance:
        d in [1-borderWidthPortion, 1] → SliderBorder colour
        d < 1-borderWidthPortion      → track gradient between
              Body.InnerOffset/OuterOffset + InnerAlpha/OuterAlpha
              (§4.9 Colors.Sliders.Body), tinted SliderTrackOverride or
              combo colour per §3.5/skin.ini.
    * Composite into the playfield under the hit circles of later objects
      (std draw order: body below its own head, follow points below all).

  Snaking (§4.9 Snaking.In/Out): draw the sub-range of path segments whose
  cumulative length falls inside [snakeStart, snakeEnd] — the cumulative
  table (StdSliderPath.cum) already exists; no re-tessellation.

  HR/Mirror: flip the PATH VERTICES (vFlip Y=384-Y) before extrusion —
  §2.5 sliderrenderer.NewBody(multiCurve, vFlip, hFlip, CircleRadius).

ACCEPTANCE for the spike (from APPROACH.md §6.3): a Bezier snake and an
overlapping aspire case render without seams/double-darkening, plus
snaking in/out, at 1080p within budget on the 2070S.
"""
from __future__ import annotations


class SliderBodyRenderer:
    """STUB — implemented by the Phase-0 spike (the NEXT task after this
    scaffold). See module docstring for the full technique."""

    def __init__(self, ctx, circle_radius_px: float):
        raise NotImplementedError(
            "slider body rendering is the Phase-0 spike — see module docstring")
