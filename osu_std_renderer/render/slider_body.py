"""Slider body renderer — the Phase-0 spike, IMPLEMENTED (was a stub).

THE TECHNIQUE (the distance-field / depth-trick tube; what the reference
renderer's sliderrenderer and lazer's SmoothPath both do):

  Distance pass (offscreen FBO, R32F colour + 24-bit depth, blending OFF,
  depth test GL_LESS):
    * For each segment of the flattened path (curves/StdSliderPath.path,
      mapped to screen px by the caller), an instanced "tent" quad extruded
      ±radius perpendicular to the segment: vertices carry d = |lateral| /
      radius (0 on the centreline, 1 at the rim) and gl_Position.z = d*2-1,
      so window depth == d.
    * At EVERY path vertex an instanced disc fan (centre d=0, rim d=1) —
      this rounds the joins between segments and provides the end caps.
    * The fragment writes d to the R32F target; the depth test keeps, per
      pixel, the SMALLEST distance to the path. The result is the exact
      union of swept circles (capsule union) with zero overdraw artifacts —
      alpha blending would double-darken self-overlaps (aspire sliders
      overlap constantly), which is why a depth buffer is mandatory.

  Shade pass (AABB quad over the distance texture → RGBA8 body FBO,
  blending OFF): shade by inv = 1-d (0 = outer rim, 1 = centreline):
      inv in [0, 34/512)                → outer drop shadow, black, alpha
                                          ramping 0 → 0.5·border.a inward
      inv in [34/512, 34/512 + border)  → SliderBorder colour; border
                                          portion = 65/512 at BorderWidth 1
                                          (§4.9 Sliders.BorderWidth 0..10)
      inv above                         → body gradient outer→inner between
                                          Shade2(base, Body.OuterOffset) and
                                          Shade2(base, Body.InnerOffset)
                                          with Outer/InnerAlpha (§4.9
                                          Colors.Sliders.Body; base =
                                          combo colour when UseHitCircleColor,
                                          else SliderTrackOverride / Body base)
  Zone edges are smoothstepped over a `blend` band (0.01 of radius, clamped
  to ≥ ~0.75 screen px so tiny radii still anti-alias); the shadow ramp
  reaching exactly 0 at the rim gives the outer-edge AA for free.
  Constants cross-checked against lazer's MIT DrawableSliderPath
  (BORDER_PORTION = 0.128 ≈ 65/512) and legacy sprite padding
  (LEGACY_CIRCLE_RADIUS/OBJECT_RADIUS ≈ 1 - 34/512 → the shadow fringe
  matches the hitcircle sprite's transparent padding, so the visible border
  edge lines up with the visible hitcircle edge).

  Composite: `draw_body` blits the shaded RGBA texture (straight alpha)
  over the scene with SRC_ALPHA/ONE_MINUS_SRC_ALPHA — the same convention
  as render/gl.py's SpriteRenderer. The body is built once per slider into
  the FBO and re-blitted every frame; snaking rebuilds it as the visible
  range grows (§4.9 Snaking.In/Out).

  Snaking: `build_body(..., snake=(start, end))` draws the sub-range of the
  path whose cumulative length lies inside [start, end]·total — the
  cumulative table is recomputed from the screen-space points (equivalent
  to StdSliderPath.cum under the uniform playfield transform), with the two
  cut endpoints interpolated. A zero-length range degenerates to one disc.

  HR/Mirror: flip the PATH VERTICES (Y=384-Y) before mapping to screen —
  §2.5 sliderrenderer.NewBody(multiCurve, vFlip, hFlip, CircleRadius).

  Deliberately NOT ported (documented for later phases): the reference's
  scene-level gl_FragDepth trick used only for SliderMerge; per-beat
  cutoff scaling (flash-to-beat); border inner/outer gradient offset
  (defaults produce a flat border anyway — §4.9 CustomGradientOffset 0).

  Perf note (measured on the 2070S @1080p, GPU-synced): 0.16–0.22 ms per
  body build including Python instance prep (91–199 path points); a full
  build+blit+readback+NVENC loop runs ~96 fps (tools/spike_slider_body.py
  --mp4). FBOs are allocated once at output size and shared; instance
  buffers are orphaned per build. The MVP does one build per slider per
  snake change and one blit per frame.

ACCEPTANCE (APPROACH.md §6.3): Bezier snake + overlapping aspire cases
render without seams/double-darkening, plus snaking in/out, at 1080p —
proven by tools/spike_slider_body.py and tests/test_slider_body.py.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import numpy as np

try:
    import moderngl
except Exception as e:  # noqa: BLE001
    raise RuntimeError("moderngl is required for the std renderer") from e

# --- legacy slider-look constants (fractions of CircleRadius) -----------------
SHADOW_PORTION = 34.0 / 512.0        # outer drop-shadow fringe
BASE_BORDER_PORTION = 65.0 / 512.0   # border thickness at BorderWidth = 1
MAX_BORDER_PORTION = 1.0 - SHADOW_PORTION
_BORDER_SLOPE = (MAX_BORDER_PORTION - BASE_BORDER_PORTION) / 9.0  # widths 1..10
AA_BAND_PORTION = 0.01               # zone-transition band
CAP_SEGMENTS = 60                    # disc fan resolution (reference LOD 50)

# §3.2 default combo colours (osu! defaults) — handy for callers/tests.
DEFAULT_COMBO_COLORS = ((255 / 255, 192 / 255, 0.0), (0.0, 202 / 255, 0.0),
                        (18 / 255, 124 / 255, 1.0), (242 / 255, 24 / 255, 57 / 255))


def shade2(rgb: tuple[float, float, float], amount: float) -> tuple[float, float, float]:
    """The reference's Color.Shade2 (stable's slider-track shading):
    amount < 0 → Darken (channel / (1 - amount)); amount > 0 → Lighten2
    (halved amount, channel*(1 + amount/2) + amount, clamped)."""
    if amount < 0:
        scale = max(1.0, 1.0 - amount)
        return (rgb[0] / scale, rgb[1] / scale, rgb[2] / scale)
    amount *= 0.5
    scale = 1.0 + 0.5 * amount
    return (min(1.0, rgb[0] * scale + amount),
            min(1.0, rgb[1] * scale + amount),
            min(1.0, rgb[2] * scale + amount))


def border_portion(border_width: float) -> float:
    """§4.9 Sliders.BorderWidth (0..10) → border thickness as a fraction of
    the radius: linear 0→base below 1, then base→(1-shadow) up to 10."""
    w = max(0.0, min(10.0, border_width))
    if w < 1.0:
        return w * BASE_BORDER_PORTION
    return (w - 1.0) * _BORDER_SLOPE + BASE_BORDER_PORTION


@dataclass(frozen=True)
class BodyStyle:
    """Colours per the plan's defaults (§3.2 skin.ini + §4.9 settings):
    white SliderBorder; body = hit-circle combo colour with the
    Inner/OuterOffset + Inner/OuterAlpha gradient semantics.

    DEFAULT GRADIENT (owner-directed, 2026-07): BRIGHTER at the
    centreline fading DARKER toward the border — the classic osu! body
    read (a faint inner glow). The previous dark-centred defaults
    (inner -0.5 / outer -0.05) read flat/uncanny; both offsets stay
    fully parameterized for skins/presets that want the inverse."""
    border_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    body_color: tuple[float, float, float] = DEFAULT_COMBO_COLORS[0]
    inner_offset: float = 0.1     # §4.9 Body.InnerOffset (centreline, lifted)
    outer_offset: float = -0.5    # §4.9 Body.OuterOffset (darker at border)
    inner_alpha: float = 0.8      # §4.9 Body.InnerAlpha
    outer_alpha: float = 0.8      # §4.9 Body.OuterAlpha
    border_width: float = 1.0     # §4.9 Sliders.BorderWidth
    alpha: float = 1.0            # object fade (multiplies everything)


@dataclass
class BodyTexture:
    """A built slider body: shaded RGBA texture (screen-sized, straight
    alpha) + the AABB actually covered, ready for draw_body()."""
    texture: "moderngl.Texture"
    aabb: tuple[float, float, float, float]   # x0, y0, x1, y1 screen px
    radius_px: float
    empty: bool = False


# --- shaders -------------------------------------------------------------------

_DIST_FRAG = """
#version 330
in float v_d;
out vec4 f_dist;
void main() { f_dist = vec4(v_d, 0.0, 0.0, 1.0); }
"""

# Segment "tent": in_pos.x ∈ [0,1] along the segment, in_pos.y ∈ [-1,1]
# across it; in_d = |in_pos.y| baked per vertex (linear per triangle).
_SEG_VERT = """
#version 330
in vec2 in_pos;
in float in_d;
in vec2 i_p1;        // segment start, screen px (top-left origin)
in float i_len;      // segment length, px
in vec2 i_dir;       // unit direction
uniform vec2 u_screen;
uniform float u_radius;
out float v_d;
void main() {
    vec2 norm = vec2(-i_dir.y, i_dir.x);
    vec2 px = i_p1 + i_dir * (in_pos.x * i_len) + norm * (in_pos.y * u_radius);
    vec2 ndc = vec2(px.x / u_screen.x * 2.0 - 1.0, 1.0 - px.y / u_screen.y * 2.0);
    gl_Position = vec4(ndc, in_d * 2.0 - 1.0, 1.0);   // window depth == d
    v_d = in_d;
}
"""

# Cap disc: unit fan (centre d=0, rim d=1) scaled by radius at each vertex.
_CAP_VERT = """
#version 330
in vec2 in_pos;
in float in_d;
in vec2 i_center;    // screen px (top-left origin)
uniform vec2 u_screen;
uniform float u_radius;
out float v_d;
void main() {
    vec2 px = i_center + in_pos * u_radius;
    vec2 ndc = vec2(px.x / u_screen.x * 2.0 - 1.0, 1.0 - px.y / u_screen.y * 2.0);
    gl_Position = vec4(ndc, in_d * 2.0 - 1.0, 1.0);
    v_d = in_d;
}
"""

_QUAD_VERT = """
#version 330
in vec2 in_pos;      // screen px (top-left origin)
uniform vec2 u_screen;
out vec2 v_uv;
void main() {
    vec2 ndc = vec2(in_pos.x / u_screen.x * 2.0 - 1.0,
                    1.0 - in_pos.y / u_screen.y * 2.0);
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = vec2(in_pos.x / u_screen.x, 1.0 - in_pos.y / u_screen.y);
}
"""

_SHADE_FRAG = """
#version 330
uniform sampler2D u_dist;
uniform vec4 u_border;       // SliderBorder, alpha = object alpha
uniform vec4 u_body_inner;   // Shade2(base, InnerOffset), InnerAlpha·alpha
uniform vec4 u_body_outer;   // Shade2(base, OuterOffset), OuterAlpha·alpha
uniform float u_border_portion;
uniform float u_blend;       // AA band, fraction of radius
in vec2 v_uv;
out vec4 f_color;

const float SHADOW = 0.06640625;   // 34/512

void main() {
    float d = texture(u_dist, v_uv).r;
    if (d >= 0.9995) discard;                   // untouched background
    float inv = 1.0 - d;                        // 0 = rim, 1 = centreline
    float border_end = SHADOW + u_border_portion;

    vec4 shadow = vec4(0.0, 0.0, 0.0,
                       0.5 * min(inv / SHADOW, 1.0) * u_border.a);
    vec4 body = mix(u_body_outer, u_body_inner,
                    clamp((inv - border_end) / max(1.0 - border_end, 1e-4),
                          0.0, 1.0));

    vec4 c = mix(shadow, u_border,
                 smoothstep(SHADOW - u_blend, SHADOW + u_blend, inv));
    c = mix(c, body,
            smoothstep(border_end - u_blend, border_end + u_blend, inv));
    f_color = c;
}
"""

_COMPOSITE_FRAG = """
#version 330
uniform sampler2D u_tex;
uniform float u_alpha;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec4 t = texture(u_tex, v_uv);
    f_color = vec4(t.rgb, t.a * u_alpha);
}
"""


def _tent_mesh() -> np.ndarray:
    """Triangle-strip tent quad: (x, y, d) with d = |y|."""
    return np.array([
        0.0, -1.0, 1.0,
        1.0, -1.0, 1.0,
        0.0,  0.0, 0.0,
        1.0,  0.0, 0.0,
        0.0,  1.0, 1.0,
        1.0,  1.0, 1.0,
    ], dtype="f4")


def _disc_mesh(slices: int = CAP_SEGMENTS) -> np.ndarray:
    """Unit disc as triangles: (x, y, d), centre d=0, rim d=1."""
    verts: list[float] = []
    for i in range(slices):
        a0 = 2.0 * math.pi * i / slices
        a1 = 2.0 * math.pi * (i + 1) / slices
        verts += [0.0, 0.0, 0.0,
                  math.cos(a0), math.sin(a0), 1.0,
                  math.cos(a1), math.sin(a1), 1.0]
    return np.array(verts, dtype="f4")


def sub_path(points: list[tuple[float, float]],
             snake_start: float, snake_end: float) -> list[tuple[float, float]]:
    """The sub-polyline whose cumulative length lies in
    [snake_start, snake_end]·total, endpoints interpolated (§4.9 snaking:
    draw a sub-range of path segments — no re-tessellation)."""
    if len(points) < 2:
        return list(points)
    cum = [0.0]
    for i in range(len(points) - 1):
        cum.append(cum[-1] + math.hypot(points[i + 1][0] - points[i][0],
                                        points[i + 1][1] - points[i][1]))
    total = cum[-1]
    a = max(0.0, min(1.0, min(snake_start, snake_end)))
    b = max(0.0, min(1.0, max(snake_start, snake_end)))
    if total <= 1e-9:
        return [points[0]]
    l0, l1 = a * total, b * total

    def interp(dist: float) -> tuple[float, float]:
        i = bisect.bisect_left(cum, dist)
        if i <= 0:
            return points[0]
        if i >= len(points):
            return points[-1]
        d0, d1 = cum[i - 1], cum[i]
        if d1 - d0 <= 1e-9:
            return points[i - 1]
        w = (dist - d0) / (d1 - d0)
        p, q = points[i - 1], points[i]
        return (p[0] + (q[0] - p[0]) * w, p[1] + (q[1] - p[1]) * w)

    out = [interp(l0)]
    i0 = bisect.bisect_right(cum, l0)
    i1 = bisect.bisect_left(cum, l1)
    for i in range(i0, i1):
        pt = points[i]
        if math.hypot(pt[0] - out[-1][0], pt[1] - out[-1][1]) > 1e-6:
            out.append(pt)
    end = interp(l1)
    if math.hypot(end[0] - out[-1][0], end[1] - out[-1][1]) > 1e-6:
        out.append(end)
    return out


class SliderBodyRenderer:
    """Builds slider bodies into a shared offscreen FBO pair and composites
    them into the scene. Build → draw must interleave per slider (the FBOs
    are reused); an FBO pool for caching static bodies is a later perf step.
    """

    _INSTANCE_RESERVE = 4096  # instances; buffers grow on demand

    def __init__(self, ctx: "moderngl.Context", width: int, height: int):
        self.ctx = ctx
        self.width = width
        self.height = height

        # distance FBO: R32F min-distance + depth for the union trick
        self._dist_tex = ctx.texture((width, height), 1, dtype="f4")
        self._dist_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self._depth_rb = ctx.depth_renderbuffer((width, height))
        self._dist_fbo = ctx.framebuffer(color_attachments=[self._dist_tex],
                                         depth_attachment=self._depth_rb)
        # shaded body FBO (straight-alpha RGBA)
        self._body_tex = ctx.texture((width, height), 4)
        self._body_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._body_fbo = ctx.framebuffer(color_attachments=[self._body_tex])

        screen = (float(width), float(height))
        self._seg_prog = ctx.program(vertex_shader=_SEG_VERT,
                                     fragment_shader=_DIST_FRAG)
        self._cap_prog = ctx.program(vertex_shader=_CAP_VERT,
                                     fragment_shader=_DIST_FRAG)
        self._shade_prog = ctx.program(vertex_shader=_QUAD_VERT,
                                       fragment_shader=_SHADE_FRAG)
        self._comp_prog = ctx.program(vertex_shader=_QUAD_VERT,
                                      fragment_shader=_COMPOSITE_FRAG)
        for prog in (self._seg_prog, self._cap_prog, self._shade_prog,
                     self._comp_prog):
            prog["u_screen"].value = screen

        self._tent_vbo = ctx.buffer(_tent_mesh().tobytes())
        self._disc_vbo = ctx.buffer(_disc_mesh().tobytes())
        self._seg_inst = ctx.buffer(reserve=self._INSTANCE_RESERVE * 20,
                                    dynamic=True)   # 2f 1f 2f = 20 bytes
        self._cap_inst = ctx.buffer(reserve=self._INSTANCE_RESERVE * 8,
                                    dynamic=True)   # 2f = 8 bytes
        self._quad_vbo = ctx.buffer(reserve=4 * 2 * 4, dynamic=True)

        self._seg_vao = ctx.vertex_array(self._seg_prog, [
            (self._tent_vbo, "2f 1f", "in_pos", "in_d"),
            (self._seg_inst, "2f 1f 2f/i", "i_p1", "i_len", "i_dir"),
        ])
        self._cap_vao = ctx.vertex_array(self._cap_prog, [
            (self._disc_vbo, "2f 1f", "in_pos", "in_d"),
            (self._cap_inst, "2f/i", "i_center"),
        ])
        self._shade_vao = ctx.vertex_array(
            self._shade_prog, [(self._quad_vbo, "2f", "in_pos")])
        self._comp_vao = ctx.vertex_array(
            self._comp_prog, [(self._quad_vbo, "2f", "in_pos")])

    # --- build -----------------------------------------------------------------

    def build_body(self, path_points: list[tuple[float, float]],
                   radius_px: float,
                   style: BodyStyle | None = None,
                   snake: tuple[float, float] = (0.0, 1.0)) -> BodyTexture:
        """Render one slider body into the shared FBO pair.

        path_points: flattened path in SCREEN px (StdSliderPath.path mapped
        through PlayfieldCamera.to_screen; HR flip applied to the osu-space
        vertices beforehand). radius_px = CircleRadius · camera scale.
        snake = (start, end) fractions of path length (§4.9 Snaking).
        """
        style = style or BodyStyle()
        pts = sub_path(list(path_points), snake[0], snake[1])
        if not pts or radius_px <= 0.0:
            return BodyTexture(self._body_tex, (0, 0, 0, 0), radius_px, empty=True)

        arr = np.asarray(pts, dtype="f4").reshape(-1, 2)
        # AABB (for the shade/composite quads), padded 2px beyond the rim
        pad = radius_px + 2.0
        x0 = max(0.0, float(arr[:, 0].min()) - pad)
        y0 = max(0.0, float(arr[:, 1].min()) - pad)
        x1 = min(float(self.width), float(arr[:, 0].max()) + pad)
        y1 = min(float(self.height), float(arr[:, 1].max()) + pad)
        if x1 <= x0 or y1 <= y0:
            return BodyTexture(self._body_tex, (0, 0, 0, 0), radius_px, empty=True)

        # instances: segments (skip degenerate) + a cap at every vertex
        deltas = arr[1:] - arr[:-1]
        lens = np.hypot(deltas[:, 0], deltas[:, 1]).astype("f4")
        keep = lens > 1e-4
        n_seg = int(keep.sum())
        if n_seg:
            seg = np.empty((n_seg, 5), dtype="f4")
            seg[:, 0:2] = arr[:-1][keep]
            seg[:, 2] = lens[keep]
            seg[:, 3:5] = deltas[keep] / lens[keep, None]
            self._write_instances(self._seg_inst, seg.tobytes())
        self._write_instances(self._cap_inst, arr.tobytes())

        ctx = self.ctx
        blend_was_on = True  # gl.py's SpriteRenderer keeps BLEND enabled
        ctx.disable(moderngl.BLEND)

        # --- distance pass (depth-trick union) ---------------------------------
        self._dist_fbo.use()
        ctx.clear(1.0, 1.0, 1.0, 1.0, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"
        self._seg_prog["u_radius"].value = radius_px
        self._cap_prog["u_radius"].value = radius_px
        if n_seg:
            self._seg_vao.render(moderngl.TRIANGLE_STRIP, instances=n_seg)
        self._cap_vao.render(moderngl.TRIANGLES, instances=len(arr))
        ctx.disable(moderngl.DEPTH_TEST)

        # --- shade pass ---------------------------------------------------------
        border = (*style.border_color, style.alpha)
        inner = (*shade2(style.body_color, style.inner_offset),
                 style.inner_alpha * style.alpha)
        outer = (*shade2(style.body_color, style.outer_offset),
                 style.outer_alpha * style.alpha)
        self._body_fbo.use()
        ctx.clear(0.0, 0.0, 0.0, 0.0)
        self._dist_tex.use(location=0)
        sp = self._shade_prog
        sp["u_dist"].value = 0
        sp["u_border"].value = border
        sp["u_body_inner"].value = inner
        sp["u_body_outer"].value = outer
        sp["u_border_portion"].value = border_portion(style.border_width)
        # ≥ ~0.75px AA even at tiny radii (deviation from the fixed 0.01)
        sp["u_blend"].value = max(AA_BAND_PORTION, 0.75 / radius_px)
        self._write_quad(x0, y0, x1, y1)
        self._shade_vao.render(moderngl.TRIANGLE_STRIP)

        if blend_was_on:
            ctx.enable(moderngl.BLEND)
        return BodyTexture(self._body_tex, (x0, y0, x1, y1), radius_px)

    # --- composite ---------------------------------------------------------------

    def draw_body(self, body: BodyTexture,
                  target_fbo: "moderngl.Framebuffer",
                  alpha: float = 1.0) -> None:
        """Blit a built body over the scene (straight-alpha blend, same
        convention as SpriteRenderer). `alpha` is an extra fade multiplier."""
        if body.empty:
            return
        ctx = self.ctx
        target_fbo.use()
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        body.texture.use(location=0)
        self._comp_prog["u_tex"].value = 0
        self._comp_prog["u_alpha"].value = alpha
        x0, y0, x1, y1 = body.aabb
        self._write_quad(x0, y0, x1, y1)
        self._comp_vao.render(moderngl.TRIANGLE_STRIP)

    # --- internals -----------------------------------------------------------------

    def _write_instances(self, buf: "moderngl.Buffer", data: bytes) -> None:
        if len(data) > buf.size:
            buf.orphan(max(len(data), buf.size * 2))
        else:
            buf.orphan()
        buf.write(data)

    def _write_quad(self, x0: float, y0: float, x1: float, y1: float) -> None:
        quad = np.array([x0, y0, x1, y0, x0, y1, x1, y1], dtype="f4")
        self._quad_vbo.write(quad.tobytes())

    def release(self) -> None:
        for obj in (self._dist_fbo, self._body_fbo, self._dist_tex,
                    self._body_tex, self._depth_rb, self._tent_vbo,
                    self._disc_vbo, self._seg_inst, self._cap_inst,
                    self._quad_vbo):
            try:
                obj.release()
            except Exception:  # noqa: BLE001
                pass
