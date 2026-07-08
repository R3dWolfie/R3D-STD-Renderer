"""§2.9 curve checks against the verbatim sliderpath port (StdSliderPath)."""
import math

from osu_std_renderer.curves import StdSliderPath


def test_linear_truncated():
    p = StdSliderPath("L|506:192", 256, 192, 250)
    assert abs(p.distance - 250) < 1e-9
    x, y = p.position_at(0.5)
    assert abs(x - 381) < 1e-6 and abs(y - 192) < 1e-6


def test_linear_extension():
    # expected longer than the raw polyline → extended along the last segment
    p = StdSliderPath("L|300:100", 100, 100, 400)
    assert abs(p.distance - 400) < 1e-9
    x, y = p.position_at(1.0)
    assert abs(x - 500) < 1e-6 and abs(y - 100) < 1e-6


def test_perfect_circle_arc():
    # quarter-ish arc through 3 points; truncated to expected length
    p = StdSliderPath("P|350:250|400:200", 300, 200, 100)
    assert abs(p.distance - 100) < 1e-6
    assert len(p.path) >= 3
    # every point must sit on the circumcircle (centre (350,200), r=50)
    for (x, y) in p.path:
        assert abs(math.hypot(x - 350, y - 200) - 50) < 0.5


def test_bezier_split_at_duplicate_anchor():
    p = StdSliderPath("B|200:0|200:0|400:100", 0, 0, None)
    assert p.distance > 0
    start, end = p.path[0], p.path[-1]
    assert start == (0.0, 0.0)
    assert abs(end[0] - 400) < 1e-6 and abs(end[1] - 100) < 1e-6


def test_catmull_default_when_no_type_token():
    # pre-v4 maps: no type char → default Catmull (§2.5 parseCurve)
    p = StdSliderPath("100:100|200:100", 0, 100, None)
    assert p.distance > 0
    assert p.path[0] == (0.0, 100.0)


def test_head_y_used():
    # regression vs the catch-shaped verbatim class (head y hardcoded 0)
    p = StdSliderPath("L|100:300", 100, 100, None)
    assert p.path[0] == (100.0, 100.0)
    assert abs(p.distance - 200) < 1e-9
