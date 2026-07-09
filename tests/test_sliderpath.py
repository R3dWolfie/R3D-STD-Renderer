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


# --- compound / multi-segment sliders (inline curve-TYPE switches) -----------

def _circumcircle(a, b, c):
    """Centre + radius of the circle through 3 non-collinear points."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay)
          + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx)
          + (cx**2 + cy**2) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def test_compound_parse_split_and_types():
    """`B|…|P|…` → the control-point list matches lazer's convertPathString +
    convertPoints: head prepended, the P point carries the inline segment
    type, and the shared junction point (start of the P segment) is the last
    point of the bezier segment too."""
    parts = "B|100:50|P|200:200|250:150|300:200".split("|")
    points_buf, segments = StdSliderPath._parse_segments(parts, 0, 0)
    assert points_buf == [(0.0, 0.0), (100.0, 50.0), (200.0, 200.0),
                          (250.0, 150.0), (300.0, 200.0)]
    assert segments == [("B", 0), ("P", 2)]

    cps = StdSliderPath("L|1:1", 0, 0, None)._build_control_points(
        points_buf, segments)
    positions = [pos for pos, _ in cps]
    types = [t for _, t in cps]
    assert positions == [(0.0, 0.0), (100.0, 50.0), (200.0, 200.0),
                         (250.0, 150.0), (300.0, 200.0)]
    # only the first point of each segment carries a definite type
    assert types == ["B", None, "P", None, None]


def test_compound_bezier_then_arc_geometry():
    """The path must trace a bezier THEN a real circular arc — not a straight
    shortcut and not a dropped segment."""
    p = StdSliderPath("B|100:50|P|200:200|250:150|300:200", 0, 0, None)
    assert p.path[0] == (0.0, 0.0)
    assert abs(p.path[-1][0] - 300) < 1e-6 and abs(p.path[-1][1] - 200) < 1e-6

    # the junction (200,200) is visited exactly on the path
    jidx = min(range(len(p.path)),
               key=lambda k: math.hypot(p.path[k][0] - 200, p.path[k][1] - 200))
    jx, jy = p.path[jidx]
    assert abs(jx - 200) < 1e-6 and abs(jy - 200) < 1e-6

    # every point at/after the junction lies on the arc's circumcircle
    centre, r = _circumcircle((200, 200), (250, 150), (300, 200))
    assert abs(centre[0] - 250) < 1e-6 and abs(centre[1] - 200) < 1e-6
    assert abs(r - 50) < 1e-6
    for x, y in p.path[jidx:]:
        assert abs(math.hypot(x - centre[0], y - centre[1]) - r) < 0.5
    # and the bezier head is a genuinely distinct (off-arc) segment
    assert any(abs(math.hypot(x - centre[0], y - centre[1]) - r) > 1.0
               for x, y in p.path[:jidx])


def test_compound_perfect_two_points_falls_back_to_bezier():
    """A P segment left with only 2 control points (its shared start + one
    point) has != 3 points → Bezier fallback (lazer calculateSubPath), so the
    path still reaches the final anchor with no crash."""
    p = StdSliderPath("B|100:100|200:200|P|300:300|400:400", 0, 0, None)
    assert p.path[0] == (0.0, 0.0)
    assert abs(p.path[-1][0] - 400) < 1e-6 and abs(p.path[-1][1] - 400) < 1e-6
    # the junction (300,300) sits on the path
    jd = min(math.hypot(x - 300, y - 300) for x, y in p.path)
    assert jd < 1e-6


def test_compound_expected_distance_truncation():
    """The stable calculateLength truncation still applies end-to-end on a
    compound path."""
    p = StdSliderPath("B|100:50|P|200:200|250:150|300:200", 0, 0, 150.0)
    assert abs(p.distance - 150.0) < 1e-6
    # position_at(1.0) is the truncated tail, still on the path
    x, y = p.position_at(1.0)
    assert math.isfinite(x) and math.isfinite(y)


def test_compound_linear_into_bezier():
    """A different type pairing (L then B) also splits and joins correctly."""
    p = StdSliderPath("L|100:0|B|100:0|150:80|200:0", 0, 0, None)
    assert p.path[0] == (0.0, 0.0)
    # the linear leg runs straight along y=0 to the junction (100,0)
    jidx = min(range(len(p.path)),
               key=lambda k: math.hypot(p.path[k][0] - 100, p.path[k][1]))
    for x, y in p.path[:jidx + 1]:
        assert abs(y) < 1e-6                       # straight line, y stays 0
    # the bezier leg bows away from y=0 (max |y| well above zero)
    assert max(abs(y) for _, y in p.path[jidx:]) > 20
    assert abs(p.path[-1][0] - 200) < 1e-6 and abs(p.path[-1][1]) < 1e-6


def test_single_segment_not_routed_through_multi():
    """A single-type curve (one type letter) must keep the byte-exact
    single-segment path — the multi route only triggers on >1 type letter."""
    single = StdSliderPath("P|350:250|400:200", 300, 200, 100)
    # identical to the classic single-P expectation (test_perfect_circle_arc)
    assert abs(single.distance - 100) < 1e-6
    for (x, y) in single.path:
        assert abs(math.hypot(x - 350, y - 200) - 50) < 0.5
