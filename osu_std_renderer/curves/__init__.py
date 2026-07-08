"""Slider curve math — RENDER_PLAN.md §2.9.

`sliderpath.py` is copied VERBATIM from the production catch renderer
(/home/foof/r3drender/osu-catch/osu_catch_renderer/sliderpath.py) — a
bit-exact port of osu-framework's PathApproximator (bezier adaptive
subdivision, Catmull detail 50, circular-arc tolerance, linear) plus
osu.Game SliderPath's expected-distance truncation/extension. Its
attribution header is preserved; do not edit it here — fixes flow through
the catch copy first.

Coverage check against the plan (§2.9), and the known gaps:

  COVERED (bit-exact):
    - CBezier: adaptive subdivision == BezierApproximator (tolerance 0.25),
      sub-bezier split at repeated anchors.
    - CCatmull: 4-point windows, 50 subdivisions per window.
    - CCirArc: circumcentre/radius/direction, segment count
      ceil(theta / (2*acos(1 - tolerance/r))); P with !=3 anchors → bezier.
    - CLine: polyline copy, duplicate-anchor skip.
    - NewMultiCurveT length semantics: truncation, extension along the last
      segment, and the stable "duplicate last point + longer expected →
      no extension" quirk (SliderPath.calculateLength).

  GAPS (documented, deliberately not rewritten):
    - Arc tolerance is 0.1 (osu-framework / lazer). The plan's §2.9 notes
      the reference's *stable* path uses 0.125 and a "float87" bit-exact
      arc variant (osuPi=3.14159274). Divergence is sub-pixel on sane
      radii; revisit if golden-frame diffs show arc drift.
    - Degenerate (collinear) P sliders fall back to BEZIER here; the plan
      says the reference treats collinear stable arcs as LINEAR. For 3
      collinear points both produce the same straight polyline.
    - Lazer multi-segment sliders (repeated anchor switching curve TYPE,
      e.g. "B|…|P|…") are not parsed; only classic single-type curves with
      bezier duplicate-anchor splits. Needed for lazer-created maps later.
    - Catmull as the DEFAULT when the type token is absent (pre-v4 maps)
      is handled by StdSliderPath below, not by the verbatim class.

`SliderPath` in the verbatim module is catch-shaped: it assumes head y=0
and applies catch's HR x-flip at parse time. std needs true 2D geometry
and applies HR as a Y-flip at PRESENTATION time (§2.5 ModifyPosition), so
`StdSliderPath` below rebuilds the control-point list (real head x,y, no
flips) and reuses the verbatim class's `_calc_path` / `_calc_length` /
`position_at` unchanged.
"""
from __future__ import annotations

from .legacy_random import RNG_SEED, LegacyRandom  # noqa: F401  (re-export)
from .sliderpath import SliderPath


class StdSliderPath(SliderPath):
    """2D std slider path: verbatim math, std-correct construction.

    curve_str: `TYPE|x:y|x:y|…` (TYPE ∈ P/L/B/C). Old maps (< v4) may omit
    the type token entirely — then every token is a point and the default
    type is Catmull (§2.5 parseCurve "default Catmull when absent").
    expected_distance None → keep the raw curve length (pixelLength==0 maps).
    """

    def __init__(self, curve_str: str, x0: float, y0: float,
                 expected_distance: float | None):
        parts = curve_str.split("|")
        first = parts[0] if parts else "B"
        if ":" in first:  # no type token → default Catmull, all tokens are points
            type_char = "C"
            point_tokens = parts
        else:
            type_char = first or "B"
            point_tokens = parts[1:]

        points: list[tuple[float, float]] = [(float(x0), float(y0))]
        for tok in point_tokens:
            if ":" not in tok:
                continue
            px, py = tok.split(":", 1)
            pt = (float(px), float(py))
            # §2.5 parseCurve: skip the first anchor if equal to the start pos
            if len(points) == 1 and pt == points[0]:
                continue
            points.append(pt)

        # deliberately NOT calling super().__init__ — it hardcodes y0=0 and
        # catch's HR x-flip; everything downstream is reused verbatim.
        self.path = self._calc_path(type_char, points)
        self.cum = [0.0]
        self.distance = 0.0
        self._calc_length(expected_distance)
