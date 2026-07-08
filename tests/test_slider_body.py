"""Slider-body spike tests — CPU-side sanity everywhere, plus a GPU
distance-field test that skips gracefully when no EGL device is available
(the CI-less run_all contract: tests must pass on any dev box)."""
from __future__ import annotations

import math

from osu_std_renderer.render.slider_body import (
    BASE_BORDER_PORTION, MAX_BORDER_PORTION, SHADOW_PORTION,
    BodyStyle, border_portion, shade2, sub_path,
)


def test_shade2_reference_semantics():
    # negative → Darken: channel / (1 - amount)   (§4.9 InnerOffset -0.5)
    assert shade2((0.9, 0.6, 0.3), -0.5) == (0.9 / 1.5, 0.6 / 1.5, 0.3 / 1.5)
    # positive → Lighten2: halved amount, ch*(1+amount/2)+amount, clamped
    r, g, b = shade2((0.4, 0.9, 1.0), 0.4)
    assert abs(r - (0.4 * 1.1 + 0.2)) < 1e-9
    assert g == 1.0 and b == 1.0          # clamped
    # zero offset is identity
    assert shade2((0.25, 0.5, 0.75), 0.0) == (0.25, 0.5, 0.75)


def test_border_portion_curve():
    assert border_portion(0.0) == 0.0
    assert abs(border_portion(0.5) - 0.5 * BASE_BORDER_PORTION) < 1e-9
    assert abs(border_portion(1.0) - BASE_BORDER_PORTION) < 1e-9
    assert abs(border_portion(10.0) - MAX_BORDER_PORTION) < 1e-9
    assert border_portion(99.0) == border_portion(10.0)   # clamped
    assert abs((SHADOW_PORTION + MAX_BORDER_PORTION) - 1.0) < 1e-9


def test_sub_path_full_and_partial():
    pts = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0)]   # total length 150
    assert sub_path(pts, 0.0, 1.0) == pts
    # snake-in to 50%: cut mid first segment → [(0,0),(75,0)]
    half = sub_path(pts, 0.0, 0.5)
    assert half[0] == (0.0, 0.0)
    assert abs(half[-1][0] - 75.0) < 1e-6 and abs(half[-1][1]) < 1e-6
    # snake-out from 80%: starts at 120 along → (100, 20)
    tail = sub_path(pts, 0.8, 1.0)
    assert abs(tail[0][0] - 100.0) < 1e-6 and abs(tail[0][1] - 20.0) < 1e-6
    assert tail[-1] == (100.0, 50.0)
    # degenerate range → a single point (renders as one disc)
    dot = sub_path(pts, 0.4, 0.4)
    assert len(dot) == 1 and abs(dot[0][0] - 60.0) < 1e-6
    # swapped bounds are normalized
    assert sub_path(pts, 1.0, 0.0) == pts


def _make_gpu():
    try:
        from osu_std_renderer.render.context import create_context
        ctx = create_context()
    except Exception:  # noqa: BLE001
        return None
    return ctx


def test_gpu_distance_field_and_union():
    """Distance-field sanity on the GPU: maxima on the centreline,
    symmetric falloff, and the depth-trick union (overlap == min distance,
    shaded overlap pixel identical to a single-pass pixel)."""
    ctx = _make_gpu()
    if ctx is None:
        print("SKIP: no EGL/GPU available")
        return
    import numpy as np

    from osu_std_renderer.render.slider_body import SliderBodyRenderer

    w, h, radius = 256, 128, 30.0
    r = SliderBodyRenderer(ctx, w, h)
    try:
        # --- straight tube: d == |lateral| / radius --------------------------
        # centreline at y=64.5 → exactly on pixel-row-64 sample centres
        r.build_body([(60.0, 64.5), (200.0, 64.5)], radius)
        dist = np.frombuffer(r._dist_fbo.read(components=1, dtype="f4"),
                             dtype="f4").reshape(h, w)[::-1]  # top-left origin
        assert dist[64, 128] < 0.02                       # centreline ≈ 0
        for k in (10, 20, 28):
            up, down = dist[64 - k, 128], dist[64 + k, 128]
            assert abs(up - k / radius) < 0.04, (k, up)
            assert abs(up - down) < 0.02                  # symmetric falloff
        assert dist[64, 128] <= dist[54, 128] <= dist[44, 128]  # monotonic
        assert dist[2, 128] >= 0.999                      # untouched bg
        # round cap beyond the endpoint: distance from (200, 64.5)
        assert abs(dist[64, 215] - 15.5 / radius) < 0.04

        # --- union: self-overlapping X — min of both strokes ------------------
        cross = [(60.0, 30.0), (200.0, 98.0), (200.0, 30.0), (60.0, 98.0)]
        body = r.build_body(cross, radius, BodyStyle(alpha=0.8))
        dist = np.frombuffer(r._dist_fbo.read(components=1, dtype="f4"),
                             dtype="f4").reshape(h, w)[::-1]
        # the crossing point of the two diagonals lies on BOTH centrelines
        assert dist[64, 130] < 0.06
        shaded = np.frombuffer(r._body_fbo.read(components=4),
                               dtype="u1").reshape(h, w, 4)[::-1]
        # overlap pixel must not double-darken: its alpha must equal the
        # alpha of a non-overlapped centreline pixel (same distance zone).
        # (88, 44) is on stroke 1's centreline, > radius away from stroke 2.
        a_overlap = int(shaded[64, 130, 3])
        a_plain = int(shaded[44, 88, 3])
        assert body.empty is False
        assert abs(a_overlap - a_plain) <= 2, (a_overlap, a_plain)
    finally:
        r.release()
        ctx.release()
