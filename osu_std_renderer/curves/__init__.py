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
    - Multi-segment / compound sliders (the curve TYPE switching mid-list,
      e.g. "B|…|P|…") ARE parsed now — StdSliderPath._parse_segments /
      _build_control_points / _convert_points / _calc_path_multi below port
      ConvertHitObjectParser.convertPathString+convertPoints and
      SliderPath.calculatePath/calculateSubPath faithfully. Single-type
      curves NEVER enter that route (see the `multi` gate in __init__), so
      their path stays byte-identical to the verbatim class's `_calc_path`.
      Remaining edge: a pre-lazer-FORMAT multi-segment CATMULL with internal
      duplicate anchors is split per-duplicate (the lazer rule) rather than
      merged (the stable "adjacent Catmull segments are one" rule); the
      renderer has no format version at this layer and no compound-Catmull
      map exists to golden against, so this is left as documented.
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
from .sliderpath import (
    SliderPath,
    approximate_bezier,
    approximate_catmull,
    approximate_circular_arc,
    approximate_linear,
)


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

        # A compound (multi-segment) slider carries MORE THAN ONE curve-type
        # letter token — an inline "…|P|…"/"…|B|…" switch. Everything else
        # (single-type curves, incl. the pre-v4 no-type default-Catmull case
        # and bezier duplicate-anchor splits) stays on the verbatim single-
        # segment path below, so those renders are byte-identical to before.
        letter_tokens = sum(1 for p in parts if p and p[0].isalpha())

        if letter_tokens > 1:
            points_buf, segments = self._parse_segments(parts, x0, y0)
            cps = self._build_control_points(points_buf, segments)
            self.path = self._calc_path_multi(cps)
        else:
            if ":" in first:  # no type token → default Catmull, all tokens points
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
                # §2.5 parseCurve: skip the first anchor if equal to the start
                if len(points) == 1 and pt == points[0]:
                    continue
                points.append(pt)

            # deliberately NOT calling super().__init__ — it hardcodes y0=0 and
            # catch's HR x-flip; everything downstream is reused verbatim.
            self.path = self._calc_path(type_char, points)

        self.cum = [0.0]
        self.distance = 0.0
        self._calc_length(expected_distance)

    # --- compound / multi-segment sliders ------------------------------------
    #
    # Faithful port of lazer's inline-type-switch handling:
    #   ppy/osu  Rulesets/Objects/Legacy/ConvertHitObjectParser.cs
    #            (convertPathString + convertPoints — segment splitting,
    #             per-point PathControlPoint.Type, the PerfectCurve arity
    #             fallback, and passing the next segment's first point as the
    #             endPoint of the current one)
    #   ppy/osu  Rulesets/Objects/SliderPath.cs
    #            (calculatePath + calculateSubPath — per-segment computation
    #             by type and the skip-first junction de-duplication)
    # lazer works in start-relative coords (readPoint subtracts the slider
    # position, the first segment is prepended with Vector2.Zero); we work in
    # ABSOLUTE coords and prepend the real head (x0,y0). That is a pure
    # translation, so the geometry, duplicate-anchor test and collinearity
    # test are all identical.

    @staticmethod
    def _parse_segments(parts: list[str], x0: float, y0: float):
        """convertPathString loop-1: fold the "T|x:y|…|T|x:y|…" tokens into a
        flat point buffer plus a list of (type_char, start_index) segments.
        The first segment is prepended with the head point."""
        points_buf: list[tuple[float, float]] = []
        segments: list[tuple[str, int]] = []
        for s in parts:
            if s and s[0].isalpha():
                segments.append((s[0], len(points_buf)))
                if len(points_buf) == 0:            # first segment → head point
                    points_buf.append((float(x0), float(y0)))
            elif ":" in s:
                px, py = s.split(":", 1)
                points_buf.append((float(px), float(py)))
        return points_buf, segments

    def _build_control_points(self, points_buf, segments):
        """convertPathString loop-2: run each segment through _convert_points
        and concatenate into one flat control-point list. The FIRST point of
        the next segment (pointsBuffer[endIndex]) is handed to the current
        segment as its endPoint for the PerfectCurve arity rule but is NOT
        emitted here — it re-appears as the next segment's own first control
        point, which is how the shared junction is formed."""
        out: list[list] = []
        ns = len(segments)
        for i in range(ns):
            type_char, start = segments[i]
            if i < ns - 1:
                end = segments[i + 1][1]
                seg_points = points_buf[start:end]
                end_point = points_buf[end] if end < len(points_buf) else None
            else:
                seg_points = points_buf[start:]
                end_point = None
            out.extend(self._convert_points(type_char, seg_points, end_point))
        return out

    @staticmethod
    def _convert_points(type_char, points, end_point):
        """convertPoints: build [pos, type|None] control points for one
        segment, forcing a type onto the last point of every implicit
        (duplicate-anchor) sub-segment. Multi-segment sliders are lazer-format
        creations, so the lazer edge rule applies: a PerfectCurve whose
        effective point count (its own points + the shared endpoint) exceeds
        3 falls back to Bezier."""
        t = type_char
        if t == "P":
            end_len = 0 if end_point is None else 1
            if len(points) + end_len > 3:
                t = "B"
        vertices = [[p, None] for p in points]
        if vertices:
            vertices[0][1] = t
        out: list[list] = []
        length = len(vertices)
        start_index = 0
        end_index = 0
        while True:
            end_index += 1
            if end_index >= length:
                break
            # keep going while no implicit segment needs to start
            if vertices[end_index][0] != vertices[end_index - 1][0]:
                continue
            # the last control point may not start a new implicit segment
            if end_index == length - 1:
                continue
            # force a type on the last point and emit the sub-segment
            vertices[end_index - 1][1] = t
            out.extend(vertices[start_index:end_index])
            start_index = end_index + 1        # skip the duplicate (implicit)
        if start_index < end_index:
            out.extend(vertices[start_index:end_index])
        return out

    @staticmethod
    def _calc_sub_path(type_char, points):
        """calculateSubPath: one segment's control points → polyline by type.
        A PerfectCurve needs exactly 3 points, else Bezier; an invalid /
        collinear arc already reverts to Bezier inside approximate_circular_arc
        (osu-framework's own fallback)."""
        if type_char == "L":
            return approximate_linear(points)
        if type_char == "P":
            return (approximate_circular_arc(points) if len(points) == 3
                    else approximate_bezier(points))
        if type_char == "C":
            return approximate_catmull(points)
        return approximate_bezier(points)      # Bezier / BSpline default

    def _calc_path_multi(self, cps):
        """calculatePath: walk the flat control-point list. A segment ends at
        every point carrying a non-null type (or the final point) and spans
        [start..i] INCLUSIVE, so consecutive segments share their junction
        point. Sub-paths are concatenated with the shared first point de-
        duplicated (skipFirst)."""
        calculated: list[tuple[float, float]] = []
        n = len(cps)
        if n == 0:
            return calculated
        start = 0
        for i in range(n):
            if cps[i][1] is None and i < n - 1:
                continue
            seg_type = cps[start][1] or "L"            # ?? PathType.LINEAR
            seg = [cps[j][0] for j in range(start, i + 1)]
            if len(seg) == 1:
                calculated.append(seg[0])
            elif len(seg) > 1:
                sub = self._calc_sub_path(seg_type, seg)
                skip_first = (bool(calculated) and bool(sub)
                              and calculated[-1] == sub[0])
                for j in range(1 if skip_first else 0, len(sub)):
                    calculated.append(sub[j])
            start = i
        return calculated
