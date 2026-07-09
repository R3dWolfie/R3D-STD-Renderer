"""Position mods — Mirror (MR) and Random (RD).

Both reposition every hit object; the replay's recorded cursor followed those
transformed positions, so we MUST reproduce lazer's transform exactly or the
render desyncs (hits read as misses). Everything here is a faithful port of
ppy/osu (MIT); each piece cites its source.

lazer's application ORDER (osu.Game/Beatmaps/WorkingBeatmap.cs
``GetPlayableBeatmap``, verified against source):

    ... IApplicableToHitObject mods (OsuModMirror, OsuModHardRock) ...  # MR here
    processor.PostProcess()                                            # STACKING
    ... IApplicableToBeatmap  mods (OsuModRandom) ...                  # RD here

So MR runs BEFORE stacking (stacking is computed on the mirrored geometry) and
RD runs AFTER stacking (stacking is computed on the ORIGINAL positions, then RD
moves the objects — the pre-RD StackHeight is kept). This module therefore
applies MR before ``set_timing``/stacking and RD after stacking; the existing
per-query stack offset in ``HitObject.modify_position`` is left untouched, so a
non-MR/RD render is byte-identical.

  * MR  — osu.Game.Rulesets.Osu/Mods/OsuModMirror.cs
          + osu.Game.Rulesets.Osu/Utils/OsuHitObjectGenerationUtils.cs
            (ReflectHorizontallyAlongPlayfield / ReflectVerticallyAlongPlayfield)
  * RD  — osu.Game.Rulesets.Osu/Mods/OsuModRandom.cs
          + osu.Game.Rulesets.Osu/Utils/OsuHitObjectGenerationUtils.Reposition.cs
          + osu.Game/Rulesets/Mods/ModRandom.cs (Seed) and the base
            OsuHitObjectGenerationUtils.cs (RandomGaussian / RotateAwayFromEdge)
          seeded by .NET System.Random (see dotnet_random.py).
"""
from __future__ import annotations

import bisect
import math
import struct

from .difficulty import Difficulty
from .dotnet_random import DotNetRandom
from .objects.base import HitObject
from .objects.slider import Slider
from .objects.spinner import Spinner

# OsuPlayfield.BASE_SIZE = new Vector2(512, 384)
BASE_X = 512.0
BASE_Y = 384.0
PLAYFIELD_CENTRE = (BASE_X / 2.0, BASE_Y / 2.0)   # (256, 192)

# Valid MR reflection settings (OsuModMirror.MirrorType).
MIRROR_HORIZONTAL = "horizontal"
MIRROR_VERTICAL = "vertical"
MIRROR_BOTH = "both"


def _f32(x: float) -> float:
    """Round-trip through IEEE-754 binary32 == C# ``(float)`` / ``MathF``."""
    return struct.unpack("f", struct.pack("f", x))[0]


# =========================================================================
# Mirror (MR) — OsuModMirror.cs
# =========================================================================
#
# ReflectHorizontallyAlongPlayfield: Position.X -> BASE_SIZE.X - X (=512-x); for
# a slider each control point (relative to the head) has its X negated. Negating
# the RELATIVE control points AND reflecting the head about the centre line is,
# in ABSOLUTE coordinates, exactly ``x -> 512 - x`` for every path point:
#   head'   = 512 - headX
#   absPt'  = head' + (-(absPt - head)) = (512 - headX) - absPt + headX = 512 - absPt
# so reflecting every stored absolute position about the centre reproduces the
# game bit-for-bit. Reflection is an isometry, so stacking (which runs AFTER MR)
# yields the identical stack indices it would on the un-mirrored map.
# ReflectVertically is the same with Y and 384.

def _reflect_point(p: tuple[float, float], flip_x: bool, flip_y: bool) -> tuple[float, float]:
    x, y = p
    if flip_x:
        x = BASE_X - x
    if flip_y:
        y = BASE_Y - y
    return (x, y)


def apply_mirror(hit_objects: list[HitObject], reflection: str) -> None:
    """MR: reflect every object's stored positions about the playfield centre.

    Must run BEFORE ``set_timing``/stacking (lazer applies MR as
    IApplicableToHitObject, before PostProcess): the slider score paths and the
    stack indices are then computed on the mirrored geometry, matching lazer.
    """
    r = (reflection or "").lower()
    flip_x = r in (MIRROR_HORIZONTAL, MIRROR_BOTH)
    flip_y = r in (MIRROR_VERTICAL, MIRROR_BOTH)
    if not (flip_x or flip_y):
        return
    for obj in hit_objects:
        obj.start_pos_raw = _reflect_point(obj.start_pos_raw, flip_x, flip_y)
        obj.end_pos_raw = _reflect_point(obj.end_pos_raw, flip_x, flip_y)
        if isinstance(obj, Slider) and obj.multi_curve is not None:
            mc = obj.multi_curve
            mc.path = [_reflect_point(p, flip_x, flip_y) for p in mc.path]
            # cumulative lengths + total distance are invariant under reflection.


# =========================================================================
# Random (RD) — OsuModRandom.cs + OsuHitObjectGenerationUtils(.Reposition).cs
# =========================================================================

# playfield_diagonal = OsuPlayfield.BASE_SIZE.LengthFast — osuTK's LengthFast is
# the Quake fast inverse-sqrt approximation (NOT exact 640), so reproduce it.
def _inverse_sqrt_fast(x: float) -> float:
    """osuTK MathHelper.InverseSqrtFast (float32 throughout)."""
    xf = _f32(x)
    xhalf = _f32(0.5 * xf)
    i = struct.unpack("<i", struct.pack("<f", xf))[0]
    i = 0x5F375A86 - (i >> 1)
    xf = struct.unpack("<f", struct.pack("<i", i & 0xFFFFFFFF))[0]
    xf = _f32(xf * _f32(1.5 - _f32(_f32(xhalf * xf) * xf)))
    return xf


# BASE_SIZE.LengthFast = 1f / InverseSqrtFast(X*X + Y*Y)
_PF_LEN_SQ = _f32(_f32(BASE_X * BASE_X) + _f32(BASE_Y * BASE_Y))   # 409600
PLAYFIELD_DIAGONAL = _f32(1.0 / _inverse_sqrt_fast(_PF_LEN_SQ))

# RotateAwayFromEdge / border constants (OsuHitObjectGenerationUtils.cs).
_PLAYFIELD_EDGE_RATIO = 0.375
_BORDER_X = _f32(BASE_X * _PLAYFIELD_EDGE_RATIO)   # 192
_BORDER_Y = _f32(BASE_Y * _PLAYFIELD_EDGE_RATIO)   # 144

# Precision.FLOAT_EPSILON (osu.Framework) — the AlmostEquals default.
_FLOAT_EPSILON = 1e-3


# --- small vector helpers (osuTK Vector2 semantics) ----------------------
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _length(v):
    return math.hypot(v[0], v[1])


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _rotate_vector(vector, rotation):
    """OsuHitObjectGenerationUtils.rotateVector."""
    angle = math.atan2(vector[1], vector[0]) + rotation
    length = _length(vector)
    return (length * math.cos(angle), length * math.sin(angle))


def _rotate_towards(initial, destination, ratio):
    """OsuHitObjectGenerationUtils.RotateVectorTowardsVector."""
    ia = math.atan2(initial[1], initial[0])
    da = math.atan2(destination[1], destination[0])
    diff = da - ia
    while diff < -math.pi:
        diff += 2 * math.pi
    while diff > math.pi:
        diff -= 2 * math.pi
    fa = ia + ratio * diff
    length = _length(initial)
    return (length * math.cos(fa), length * math.sin(fa))


def _rotate_away_from_edge(prev_pos, pos_rel, ratio=0.5):
    """OsuHitObjectGenerationUtils.RotateAwayFromEdge."""
    rrd = 0.0
    if prev_pos[0] < PLAYFIELD_CENTRE[0]:
        rrd = max((_BORDER_X - prev_pos[0]) / _BORDER_X, rrd)
    else:
        rrd = max((prev_pos[0] - (BASE_X - _BORDER_X)) / _BORDER_X, rrd)
    if prev_pos[1] < PLAYFIELD_CENTRE[1]:
        rrd = max((_BORDER_Y - prev_pos[1]) / _BORDER_Y, rrd)
    else:
        rrd = max((prev_pos[1] - (BASE_Y - _BORDER_Y)) / _BORDER_Y, rrd)
    dest = _sub(PLAYFIELD_CENTRE, prev_pos)
    return _rotate_towards(pos_rel, dest, min(1.0, rrd * ratio))


def _clamp_to_playfield_with_padding(pos, padding):
    return (_clamp(pos[0], padding, BASE_X - padding),
            _clamp(pos[1], padding, BASE_Y - padding))


def _angle_difference(a1, a2):
    """OsuHitObjectGenerationUtils.getAngleDifference — abs diff in [0, pi)."""
    diff = math.fmod(abs(a1 - a2), math.pi * 2)
    return min(diff, math.pi * 2 - diff)


def _path_interp(apath, cum, distance, progress):
    """Absolute position at ``progress`` along a (transformed) flattened path,
    mirroring StdSliderPath.position_at (same cum/lerp)."""
    d = max(0.0, min(1.0, progress)) * distance
    i = bisect.bisect_left(cum, d)
    if i <= 0:
        return apath[0]
    if i >= len(apath):
        return apath[-1]
    d0, d1 = cum[i - 1], cum[i]
    if d1 - d0 <= 1e-9:
        return apath[i - 1]
    w = (d - d0) / (d1 - d0)
    a, b = apath[i - 1], apath[i]
    return (a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w)


# --- working object ------------------------------------------------------
class _WObj:
    """Mirror of OsuHitObjectGenerationUtils.WorkingObject, adapted to this
    renderer's absolute-path slider model. Sliders keep a working COPY of the
    flattened path (``apath``); the shared ``cum``/``distance`` are isometry-
    invariant so are reused. head == apath[0] at all times."""

    __slots__ = ("obj", "is_slider", "is_spinner", "radius", "repeat_count",
                 "apath", "cum", "distance", "rotation_original",
                 "position_modified", "end_position_modified")

    def __init__(self, obj: HitObject, radius: float):
        self.obj = obj
        self.is_slider = isinstance(obj, Slider)
        self.is_spinner = isinstance(obj, Spinner)
        self.radius = radius
        self.position_modified = obj.start_pos_raw
        self.end_position_modified = obj.end_pos_raw
        self.rotation_original = 0.0
        if self.is_slider:
            mc = obj.multi_curve
            self.apath = list(mc.path)
            self.cum = mc.cum
            self.distance = mc.distance
            self.repeat_count = obj.repeat_count
        else:
            self.apath = None
            self.cum = None
            self.distance = 0.0
            self.repeat_count = 1

    # slider geometry (all absolute; head == apath[0]) -------------------
    def head(self):
        return self.apath[0]

    def _rel_end(self):
        h = self.apath[0]
        e = self.apath[-1]
        return (e[0] - h[0], e[1] - h[1])

    def slider_rotation(self):
        """getSliderRotation: atan2 of Path.PositionAt(1) (raw path end,
        relative to head — NOT repeat-aware)."""
        re = self._rel_end()
        return math.atan2(re[1], re[0])

    def end_position(self):
        """Slider.EndPosition (repeat-aware): head for even repeats, path end
        for odd — matches this renderer's end_pos_raw convention."""
        h = self.apath[0]
        if self.repeat_count % 2 == 1:
            return self.apath[-1]
        return h

    def flip_horizontal(self):
        """FlipSliderInPlaceHorizontally: negate relative X of control points ==
        reflect the absolute path about the vertical line through the head."""
        hx = self.apath[0][0]
        self.apath = [(2.0 * hx - p[0], p[1]) for p in self.apath]

    def rotate(self, rotation):
        """RotateSlider: rotate control points about the head."""
        h = self.apath[0]
        out = [h]
        for p in self.apath[1:]:
            out.append(_add(h, _rotate_vector(_sub(p, h), rotation)))
        self.apath = out

    def set_head(self, new_head):
        """slider.Position = new_head — translate the whole path."""
        h = self.apath[0]
        dx, dy = new_head[0] - h[0], new_head[1] - h[1]
        self.apath = [(p[0] + dx, p[1] + dy) for p in self.apath]

    def calculated_path_rel(self):
        h = self.apath[0]
        return [(p[0] - h[0], p[1] - h[1]) for p in self.apath]

    def centre_of_mass(self):
        """calculateCentreOfMass — relative to head."""
        h = self.apath[0]

        def rel_at(prog):
            ap = _path_interp(self.apath, self.cum, self.distance, prog)
            return (ap[0] - h[0], ap[1] - h[1])

        sample_step = 50.0
        if self.distance <= sample_step:
            e = rel_at(1.0)
            return (e[0] / 2.0, e[1] / 2.0)
        count = 0
        sx = sy = 0.0
        i = 0.0
        while i < self.distance:
            r = rel_at(i / self.distance)
            sx += r[0]
            sy += r[1]
            count += 1
            i += sample_step
        return (sx / count, sy / count)

    def possible_movement_bounds(self):
        """CalculatePossibleMovementBounds -> (left, top, right, bottom).
        Width = right-left, Height = bottom-top (may be negative)."""
        rel = self.calculated_path_rel()
        min_x = min(p[0] for p in rel)
        max_x = max(p[0] for p in rel)
        min_y = min(p[1] for p in rel)
        max_y = max(p[1] for p in rel)
        r = self.radius
        min_x -= r
        min_y -= r
        max_x += r
        max_y += r
        left = -min_x
        right = BASE_X - max_x
        top = -min_y
        bottom = BASE_Y - max_y
        return left, top, right, bottom


class _RandomMod:
    """Holds the seeded RNG + AngleSharpness, exposing the exact draw helpers
    OsuModRandom uses (so the number stream is consumed in the game's order)."""

    def __init__(self, seed: int, angle_sharpness: float = 7.0):
        self.rng = DotNetRandom(seed)
        self.angle_sharpness = float(angle_sharpness)
        self.as_max = 10.0     # AngleSharpness.MaxValue
        self.as_default = 7.0  # AngleSharpness.Default

    def random_gaussian(self, mean, std_dev):
        """OsuHitObjectGenerationUtils.RandomGaussian (Box-Muller)."""
        x1 = 1.0 - self.rng.next_double()
        x2 = 1.0 - self.rng.next_double()
        std_normal = math.sqrt(-2.0 * math.log(x1)) * math.sin(2.0 * math.pi * x2)
        return mean + std_dev * _f32(std_normal)

    def get_random_offset(self, std_dev):
        """OsuModRandom.getRandomOffset."""
        custom_multiplier = ((1.5 * self.as_max - self.angle_sharpness)
                             / (1.5 * self.as_max - self.as_default))
        return self.random_gaussian(0.0, std_dev * custom_multiplier)

    def get_relative_target_angle(self, target_distance, offset, flow_direction):
        """OsuModRandom.getRelativeTargetAngle (customOffsetX is added TWICE —
        once to target_distance and again inside the exp; that is the game's
        actual behaviour)."""
        angle_sharpness = self.angle_sharpness / self.as_max
        angle_wideness = 1.0 - angle_sharpness
        custom_offset_x = angle_sharpness * 100.0 - 70.0
        custom_offset_y = angle_wideness * 0.25 - 0.075
        target_distance += custom_offset_x
        angle = (2.16 / (1.0 + 200.0 * math.exp(0.036 * (target_distance - 310.0 + custom_offset_x)))
                 + 0.5)
        angle += offset + custom_offset_y
        relative_angle = math.pi - angle
        return -relative_angle if flow_direction else relative_angle


# --- combo info (OsuHitObjectGenerationUtils reads post-PreProcess combo) --
def _combo_info(hit_objects):
    """IHasComboInformation.UpdateComboInformation over the RAW file new-combo
    flags: IndexInCurrentCombo (0-based) resets to 0 on the first object and on
    every raw NewCombo. Returns [(index_in_current_combo, new_combo_raw)]."""
    out = []
    last_index = -1
    for i, obj in enumerate(hit_objects):
        nc = bool(getattr(obj, "file_new_combo", obj.new_combo))
        idx = 0 if (i == 0 or nc) else last_index + 1
        out.append((idx, nc))
        last_index = idx
    return out


def _is_hit_object_on_beat(timings, obj, downbeats_only=False):
    """OsuHitObjectGenerationUtils.IsHitObjectOnBeat."""
    tp = timings.get_original_point_at(obj.start_time)
    time_since = obj.start_time - tp.time
    beat_length = tp.beat_length_base
    if downbeats_only:
        beat_length *= tp.signature
    if beat_length <= 0:
        return False
    return math.fmod(abs(time_since + 1.0), beat_length) < 2.0


def _orig_slider_rotation(obj):
    """getSliderRotation on the ORIGINAL (pre-flip) slider geometry."""
    end = obj.multi_curve.position_at(1.0)
    re = (end[0] - obj.start_pos_raw[0], end[1] - obj.start_pos_raw[1])
    return math.atan2(re[1], re[0])


def _generate_position_infos(hit_objects):
    """OsuHitObjectGenerationUtils.GeneratePositionInfos — on ORIGINAL geometry.
    Returns dicts {relative_angle, distance, rotation}."""
    infos = []
    prev_pos = PLAYFIELD_CENTRE
    prev_angle = 0.0
    for obj in hit_objects:
        pos = obj.start_pos_raw
        rel = _sub(pos, prev_pos)
        abs_angle = math.atan2(rel[1], rel[0])
        rel_angle = abs_angle - prev_angle
        info = {"relative_angle": rel_angle,
                "distance": _length(rel),
                "rotation": 0.0}
        if isinstance(obj, Slider):
            slider_rot = _orig_slider_rotation(obj)
            info["rotation"] = slider_rot - abs_angle
            abs_angle = slider_rot
        infos.append(info)
        prev_pos = obj.end_pos_raw   # EndPosition (repeat-aware)
        prev_angle = abs_angle
    return infos


def _should_start_new_section(hit_objects, combo, timings, i, rmod):
    if i == 0:
        return True
    prev_started_combo = combo[max(0, i - 2)][0] > 1 and combo[i - 1][1]
    prev_on_downbeat = _is_hit_object_on_beat(timings, hit_objects[i - 1], True)
    prev_on_beat = _is_hit_object_on_beat(timings, hit_objects[i - 1], False)
    # C# short-circuit || (draws only where the game draws):
    if prev_started_combo and rmod.rng.next_double() < 0.6:
        return True
    if prev_on_downbeat:
        return True
    if prev_on_beat and rmod.rng.next_double() < 0.4:
        return True
    return False


def _should_apply_flow_change(combo, i, rmod):
    prev_started_combo = combo[max(0, i - 2)][0] > 1 and combo[i - 1][1]
    return prev_started_combo and rmod.rng.next_double() < 0.6


def _compute_modified_position(cur, previous, before_previous, info):
    """OsuHitObjectGenerationUtils.Reposition.computeModifiedPosition."""
    previous_absolute_angle = 0.0
    if previous is not None:
        if previous.is_slider:
            previous_absolute_angle = previous.slider_rotation()
        else:
            earliest = (before_previous.end_position_modified
                        if before_previous is not None else PLAYFIELD_CENTRE)
            rel = _sub(previous.position_modified, earliest)
            previous_absolute_angle = math.atan2(rel[1], rel[0])

    absolute_angle = previous_absolute_angle + info["relative_angle"]
    dist = info["distance"]
    pos_rel = (dist * math.cos(absolute_angle), dist * math.sin(absolute_angle))

    last_end = previous.end_position_modified if previous is not None else PLAYFIELD_CENTRE
    pos_rel = _rotate_away_from_edge(last_end, pos_rel)
    cur.position_modified = _add(last_end, pos_rel)

    if not cur.is_slider:
        return

    absolute_angle = math.atan2(pos_rel[1], pos_rel[0])
    com_original = cur.centre_of_mass()
    com_modified = _rotate_vector(
        com_original, info["rotation"] + absolute_angle - cur.slider_rotation())
    com_modified = _rotate_away_from_edge(cur.position_modified, com_modified)
    relative_rotation = (math.atan2(com_modified[1], com_modified[0])
                         - math.atan2(com_original[1], com_original[0]))
    if abs(relative_rotation) > _FLOAT_EPSILON:
        cur.rotate(relative_rotation)


def _clamp_circle(cur):
    prev = cur.position_modified
    clamped = _clamp_to_playfield_with_padding(prev, cur.radius)
    cur.position_modified = clamped
    cur.end_position_modified = clamped
    return _sub(clamped, prev)


def _clamp_slider(cur):
    left, top, right, bottom = cur.possible_movement_bounds()
    width = right - left
    height = bottom - top
    if width < 0 or height < 0:
        current_rotation = cur.slider_rotation()
        diff1 = _angle_difference(cur.rotation_original, current_rotation)
        diff2 = _angle_difference(cur.rotation_original + math.pi, current_rotation)
        if diff1 < diff2:
            cur.rotate(cur.rotation_original - cur.slider_rotation())
        else:
            cur.rotate(cur.rotation_original + math.pi - cur.slider_rotation())
        left, top, right, bottom = cur.possible_movement_bounds()
        width = right - left
        height = bottom - top

    prev = cur.position_modified
    new_x = (_clamp(left, 0.0, BASE_X) if width < 0
             else _clamp(prev[0], left, right))
    new_y = (_clamp(top, 0.0, BASE_Y) if height < 0
             else _clamp(prev[1], top, bottom))
    cur.set_head((new_x, new_y))
    cur.position_modified = (new_x, new_y)
    cur.end_position_modified = cur.end_position()
    return _sub(cur.position_modified, prev)


def _apply_decreasing_shift(objs, shift):
    n = len(objs)
    for i, wo in enumerate(objs):
        factor = (n - i) / float(n + 1)
        pos = _add(wo.position_modified, (shift[0] * factor, shift[1] * factor))
        wo.position_modified = _clamp_to_playfield_with_padding(pos, wo.radius)
        wo.end_position_modified = wo.position_modified


def apply_random(hit_objects: list[HitObject], seed: int, diff: Difficulty,
                 timings, version: int, angle_sharpness: float = 7.0) -> None:
    """RD: reposition objects with the seeded RNG, EXACTLY as OsuModRandom.

    Runs AFTER stacking (lazer order); the pre-RD stack indices in each object's
    ``stack_index_map`` are kept, so ``modify_position`` still applies the
    original StackOffset on top of the new position — matching the game.
    """
    if not hit_objects:
        return
    radius = diff.circle_radius_l   # == lazer OsuHitObject.Radius
    rmod = _RandomMod(seed, angle_sharpness)

    # 1) position infos from the ORIGINAL geometry
    infos = _generate_position_infos(hit_objects)
    combo = _combo_info(hit_objects)

    # 2) working geometry (mutated by the main-loop flips + reposition)
    work = [_WObj(obj, radius) for obj in hit_objects]

    # 3) OsuModRandom.ApplyToBeatmap main loop (fixes RelativeAngle/Distance,
    #    flips sliders in place). RNG draw order is load-bearing.
    section_offset = 0.0
    flow_direction = False
    for i, obj in enumerate(hit_objects):
        if _should_start_new_section(hit_objects, combo, timings, i, rmod):
            section_offset = rmod.get_random_offset(0.0008)
            flow_direction = not flow_direction

        if work[i].is_slider and rmod.rng.next_double() < 0.5:
            work[i].flip_horizontal()

        if i == 0:
            infos[0]["distance"] = rmod.rng.next_double() * BASE_Y / 2.0
            infos[0]["relative_angle"] = rmod.rng.next_double() * 2.0 * math.pi - math.pi
        else:
            flow_change_offset = 0.0
            one_time_offset = rmod.get_random_offset(0.002)
            if _should_apply_flow_change(combo, i, rmod):
                flow_change_offset = rmod.get_random_offset(0.002)
                flow_direction = not flow_direction
            total_offset = ((section_offset + one_time_offset) * infos[i]["distance"]
                            + flow_change_offset * (PLAYFIELD_DIAGONAL - infos[i]["distance"]))
            infos[i]["relative_angle"] = rmod.get_relative_target_angle(
                infos[i]["distance"], total_offset, flow_direction)

    # 4) OsuHitObjectGenerationUtils.RepositionHitObjects
    for wo in work:
        wo.rotation_original = wo.slider_rotation() if wo.is_slider else 0.0
        wo.position_modified = wo.head() if wo.is_slider else wo.obj.start_pos_raw
        wo.end_position_modified = wo.end_position() if wo.is_slider else wo.obj.end_pos_raw

    previous = None
    for i in range(len(work)):
        cur = work[i]
        if cur.is_spinner:
            previous = cur
            continue
        before_previous = work[i - 2] if i > 1 else None
        _compute_modified_position(cur, previous, before_previous, infos[i])
        shift = _clamp_slider(cur) if cur.is_slider else _clamp_circle(cur)
        if shift != (0.0, 0.0):
            to_shift = []
            j = i - 1
            while j >= i - 10 and j >= 0:
                if work[j].is_slider or work[j].is_spinner:
                    break
                to_shift.append(work[j])
                j -= 1
            if to_shift:
                _apply_decreasing_shift(to_shift, shift)
        previous = cur

    # 5) write the repositioned geometry back and rebuild moved slider paths
    for wo in work:
        obj = wo.obj
        if wo.is_spinner:
            continue
        if wo.is_slider:
            obj.multi_curve.path = wo.apath
            obj.start_pos_raw = wo.apath[0]
            # cum/distance are isometry-invariant; rebuild score path/ticks/end.
            obj.set_timing(timings, version)
        else:
            obj.start_pos_raw = wo.position_modified
            obj.end_pos_raw = wo.position_modified
