"""std's sprite renderer on the Metal core: the same public surface as
render/gl.py SpriteRenderer, so the scene, the HUD and the results screen do
not know which one they are drawing with.

A frame is RECORDED, then replayed at commit: every pass that draws into a
texture (slider bodies, the health bar) goes first, then one pass draws the
whole scene. The scene pass is never interrupted, which is what a tile-based
GPU wants; the callers still issue their draws in the order they always did."""
from __future__ import annotations

import numpy as np

from .. import perf
from ..gl import Sprite, plan_texture_batches
from . import core as mc

_UNITS = 16

# render/gl.py _BAR_FRAG in MSL, operation for operation (it must be compiled
# with safe maths: q8 settles rounding ties with fma). The fragment at texture
# row r holds the bar's row r, exactly like an uploaded image.
_BAR_MSL = """
#include <metal_stdlib>
using namespace metal;
struct BarU { float4 pos; float4 p1; float4 p2; float4 bar; float4 glow; };
// pos = pos_a.xy, pos_b.xy | p1 = a, b, radius, agp | p2 = alpha_mult, scale, margin, w
// bar = rgb, xgrad | glow = rgba
struct V { float4 pos [[position]]; };
vertex V bar_vs(uint vid [[vertex_id]]) {
    float2 p = float2((vid << 1) & 2, vid & 2);
    V o; o.pos = float4(p * 2.0 - 1.0, 0.0, 1.0); return o;
}
static inline float q8(float v) {
    float p = v * 255.0;
    float err = fma(v, 255.0, -p);
    float fl = floor(p);
    float frac = (p - fl) + err;
    if (frac > 0.5) return (fl + 1.0) / 255.0;
    if (frac < 0.5) return fl / 255.0;
    return (fmod(fl, 2.0) == 0.0 ? fl : fl + 1.0) / 255.0;
}
fragment float4 bar_fs(V in [[stage_in]], texture2d<float> field [[texture(0)]],
                       constant BarU& u [[buffer(2)]]) {
    uint2 ij = uint2(in.pos.xy);
    float2 fs = field.read(ij).rg;
    float d = fs.r, sp = fs.g;
    float u_a = u.p1.x, u_b = u.p1.y, u_radius = u.p1.z, u_agp = u.p1.w;
    float xx = (float(ij.x) + 0.5) / u.p2.y - u.p2.z;
    float yy = (float(ij.y) + 0.5) / u.p2.y - u.p2.z;
    float2 p = float2(xx, yy);
    float d_clipped = clamp(d, 0.0f, u_radius);
    float D;
    if (u_b <= u_a + 1e-9) {
        D = clamp(length(p - u.pos.xy), 0.0f, u_radius);
    } else {
        bool inside = (u_a <= 0.0) ? (sp <= u_b) : (sp >= u_a && sp <= u_b);
        float da = clamp(length(p - u.pos.xy), 0.0f, u_radius);
        float db = clamp(length(p - u.pos.zw), 0.0f, u_radius);
        D = min(da, db);
        if (inside) D = d_clipped;
    }
    float core = clamp((u_radius - u_agp) - D, 0.0f, 1.0f);
    float inv  = 1.0 - core;
    float ga = clamp(1.0f - (D - u_radius + u_agp) / max(u_agp, 1e-9f), 0.0f, 1.0f);
    ga = ga * ga; ga = ga * ga; ga = ga * ga;
    float3 rgb = core * u.bar.rgb + inv * u.glow.rgb;
    float a = core + inv * (ga * u.glow.a);
    if (u.bar.w != 0.0) a *= 0.8f + 0.2f * clamp(xx / max(u.p2.w, 1e-9f), 0.0f, 1.0f);
    a *= u.p2.x;
    a = clamp(a, 0.0f, 1.0f);
    return float4(q8(rgb.r), q8(rgb.g), q8(rgb.b), q8(a));
}
"""


class _Ctx:
    """The one piece of the GL context other modules reach for: `scissor`, as
    the storyboard renderer sets it (GL convention: origin bottom-left, None to
    reset). Recorded into the scene pass at the point it is set."""

    def __init__(self, spr):
        self._spr = spr
        self._rect = None

    @property
    def scissor(self):
        return self._rect

    @scissor.setter
    def scissor(self, rect) -> None:
        self._rect = rect
        spr = self._spr
        core = spr.core
        if rect is None:
            spr.add_scene(lambda: core.scissor())
        else:
            x, y, w, h = (int(v) for v in rect)
            spr.add_scene(lambda: core.scissor(x, y, w, h))   # same row order as GL


# framebuffer -> texture copy (render/gl.py blit_texture_from): rows flipped,
# because a frame is stored bottom row first and an image texture top row
# first; alpha forced to 1, which is what a 3-component texture samples as
_COPY_MSL = """
#include <metal_stdlib>
using namespace metal;
struct V { float4 pos [[position]]; };
vertex V copy_vs(uint vid [[vertex_id]]) {
    float2 p = float2((vid << 1) & 2, vid & 2);
    V o; o.pos = float4(p * 2.0 - 1.0, 0.0, 1.0); return o;
}
fragment float4 copy_fs(V in [[stage_in]], texture2d<float> src [[texture(0)]]) {
    uint2 ij = uint2(in.pos.xy);
    return float4(src.read(uint2(ij.x, src.get_height() - 1 - ij.y)).rgb, 1.0);
}
"""


class MetalSpriteRenderer:
    is_metal = True

    def __init__(self, width: int, height: int, core: "mc.Core | None" = None,
                 ring: int = 12):
        """With `core` given this renderer draws into a texture of its own on
        that core (the results card at supersample size) instead of the frame."""
        self.width, self.height = width, height
        # GL's row order throughout, so every edge and every render-target
        # convention of the GL renderer carries over unchanged
        self.core = core or mc.Core(width, height, ring, bottom_up=True)
        self._own_core = core is None
        self._target = 0 if core is None else self.core.tex_create(
            width, height, mc.RGBA8, target=True)
        self._flushed = False
        self._tex: dict[str, int] = {}
        self._tex_shape: dict[str, tuple] = {}
        self._white = self.core.tex_create(
            1, 1, mc.RGBA8, np.full((1, 1, 4), 255, np.uint8))
        self.post_xform = None
        self._pre: list = []          # passes into textures, replayed first
        self._scene: list = []        # the scene pass, in call order
        self._clear = (0.0, 0.0, 0.0, 1.0)
        self._open = False
        self._latency = 3
        self._resets: list = []       # per-frame hooks of the passes built on this renderer
        # the scene and the passes hand these to each other on the GL renderer;
        # here they only need to exist
        self.fbo = None
        self.color_tex = self._target      # what a later pass samples (0 = the frame)
        self.ctx = _Ctx(self)

    # ---- textures ----
    def upload_texture(self, key: str, rgba: np.ndarray, clamp: bool = False,
                       mipmaps: bool = True) -> None:
        """rgba: HxWx4 uint8, row 0 at the top. Re-uploading a key replaces it."""
        h, w = rgba.shape[:2]
        old = self._tex.get(key)
        # Always a NEW texture, never an in-place rewrite: frames already
        # committed may not have run yet and would sample the new contents
        # (seen as wrong numbers on the results card while they roll). The
        # frames in flight keep the old texture alive until they finish.
        if old is not None:
            self.core.tex_free(old)
        flags = (mc.MIP if mipmaps else 0) | (mc.CLAMP if clamp else 0)
        self._tex[key] = self.core.tex_create(w, h, mc.RGBA8, rgba, mipmaps, flags)
        self._tex_shape[key] = (h, w, clamp, mipmaps)

    def write_texture(self, key: str, rgba: np.ndarray, clamp: bool = False) -> None:
        self.upload_texture(key, rgba, clamp=clamp, mipmaps=False)

    def write_texture_rgb(self, key: str, rgb: np.ndarray) -> None:
        h, w = rgb.shape[:2]
        rgba = np.empty((h, w, 4), np.uint8)
        rgba[..., :3] = rgb
        rgba[..., 3] = 255
        self.upload_texture(key, rgba, mipmaps=False)

    def has_texture(self, key: str) -> bool:
        return key in self._tex

    def release_texture(self, key: str) -> None:
        tid = self._tex.pop(key, None)
        self._tex_shape.pop(key, None)
        if tid is not None:
            self.core.tex_free(tid)

    # ---- the Argon health bar (render/gl.py render_bar, same arithmetic) ----
    def ensure_bar_field(self, d: np.ndarray, sp: np.ndarray, scale: float,
                         margin: float, width_l: float) -> None:
        if getattr(self, "_bar_ready", False):
            return
        h, w = d.shape
        packed = np.empty((h, w, 2), dtype="f4")
        packed[..., 0] = d
        packed[..., 1] = sp
        c = self.core
        self._bar_field = c.tex_create(w, h, mc.RG32F, packed,
                                       flags=mc.NEAREST | mc.CLAMP)
        self._bar_pipe = c.pipe_create(_BAR_MSL, "bar_vs", "bar_fs", mc.RGBA8,
                                       mc.BLEND_OFF, safe_math=True)
        self._bar_const = (float(scale), float(margin), float(width_l))
        self._bar_hw = (w, h)
        self._bar_ready = True

    def render_bar(self, key: str, a: float, b: float, pos_a, pos_b,
                   radius: float, glow_portion: float, bar_rgb, glow_rgba,
                   xgrad: bool = False, alpha_mult: float = 1.0) -> None:
        with perf.T("hud_health_gpu"):
            w, h = self._bar_hw
            tid = self._tex.get(key)
            if tid is None:
                tid = self.core.tex_create(w, h, mc.RGBA8, target=True)
                self._tex[key] = tid
                self._tex_shape[key] = (h, w, False, False)
            scale, margin, width_l = self._bar_const
            u = np.array([pos_a[0], pos_a[1], pos_b[0], pos_b[1],
                          a, b, radius, radius * glow_portion,
                          alpha_mult, scale, margin, width_l,
                          bar_rgb[0], bar_rgb[1], bar_rgb[2], 1.0 if xgrad else 0.0,
                          glow_rgba[0], glow_rgba[1], glow_rgba[2], glow_rgba[3]], "f4")
            c, pipe, field = self.core, self._bar_pipe, self._bar_field

            def run():
                c.set_pass(tid, (0.0, 0.0, 0.0, 0.0))
                c.draw(pipe, 3, uniforms=u, textures=(field,))
            self.add_pre(run)

    # ---- drawing ----
    def begin(self, clear=(0.0, 0.0, 0.0)) -> None:
        self._clear = (float(clear[0]), float(clear[1]), float(clear[2]), 1.0)
        self._pre.clear()
        self._scene.clear()
        self._open = True
        self._flushed = False
        for hook in self._resets:
            hook()

    def add_frame_reset(self, hook) -> None:
        self._resets.append(hook)

    def add_pre(self, op) -> None:
        """A pass into a texture, replayed before the scene pass opens."""
        self._pre.append(op)

    def add_scene(self, op) -> None:
        """A draw inside the scene pass, at this point in the draw order."""
        self._scene.append(("call", op))

    def draw_hit_error_meter(self, state, opacity=1.0) -> None:
        from ..hit_error_painter import HitErrorMeterPainter
        if not hasattr(self, '_hit_error_painter'):
            self._hit_error_painter = HitErrorMeterPainter(self)
        self._hit_error_painter.draw(state, opacity)

    def draw(self, sprites: "list[Sprite]") -> None:
        with perf.T("gl_sprites_draw"):
            perf.count("sprites", len(sprites))
            if self.post_xform is not None:
                sprites = [self.post_xform(sp) for sp in sprites]
            if not sprites:
                return
            normal: list = []
            additive: list = []
            for sp in sprites:
                (additive if sp.additive else normal).append(sp)
            ordered = normal + additive if additive else sprites
            n_norm = len(normal)
            units, batches = plan_texture_batches(ordered, n_norm, _UNITS)
            flat: list = []
            for sp, u in zip(ordered, units):
                c = sp.color
                o = sp.uv_off
                s = sp.uv_scale
                flat += (sp.x, sp.y, sp.w, sp.h, sp.rotation, c[0], c[1], c[2],
                         c[3], o[0], o[1], s[0], s[1], u)
            inst = np.array(flat, dtype="f4").reshape(len(ordered), 14)
            tex, white = self._tex, self._white
            for first, end, keys in batches:
                ids = [tex.get(k, white) if k else white for k in keys]
                self._scene.append(("sprites", inst[first:end], ids,
                                    first >= n_norm and n_norm < len(ordered)))
                perf.count("draw_calls")

    def _replay(self) -> None:
        """Encode this renderer's recorded frame, once: passes into textures,
        then its own pass. Renderers sharing a core share the open frame."""
        if self._flushed:
            return
        c = self.core
        c.ensure_frame()
        for op in self._pre:
            op()
        c.set_pass(self._target, self._clear)
        for op in self._scene:
            if op[0] == "sprites":
                c.sprites(op[1], op[2], op[3])
            else:
                op[1]()
        self._open = False
        self._flushed = True

    def blit_texture_from(self, key: str, src: "MetalSpriteRenderer") -> None:
        """Make texture `key` hold `src`'s frame as drawn so far, on the GPU
        (render/gl.py blit_texture_from: same texels, same sampling state)."""
        c = self.core
        src._replay()
        w, h = src.width, src.height
        tid = self._tex.get(key)
        if tid is None or self._tex_shape.get(key) != (h, w, False, False):
            if tid is not None:
                c.tex_free(tid)
            tid = c.tex_create(w, h, mc.RGBA8, target=True)
            self._tex[key] = tid
            self._tex_shape[key] = (h, w, False, False)
        if getattr(self, "_copy_pipe", None) is None:
            self._copy_pipe = c.pipe_create(_COPY_MSL, "copy_vs", "copy_fs",
                                            mc.RGBA8, mc.BLEND_OFF)
        c.set_pass(tid, (0.0, 0.0, 0.0, 1.0))
        c.draw(self._copy_pipe, 3, textures=(src._target,))

    # ---- frames out ----
    def read_yuv_async(self):
        """yuv420p bytes of the frame submitted `_latency` frames ago (a view of
        the GPU's buffer, rows bottom-up), or None while the pipeline fills."""
        with perf.T("readback"):
            self._replay()
            self.core.frame_commit(True)
            return self.core.frame_acquire(self._latency, False, True)

    def read_yuv_drain(self) -> list:
        out = []
        with perf.T("readback"):
            while self.core.in_flight() > 0:
                out.append(self.core.frame_acquire(0, True, True))
        return out

    def read_rgb_async(self):
        """RGB fallback (R3D_STD_GPU_YUV off): (h, w, 3) top-down, copied."""
        with perf.T("readback"):
            self._replay()
            self.core.frame_commit(False)
            f = self.core.frame_acquire(self._latency, False, False)
            return None if f is None else np.ascontiguousarray(f[::-1, :, :3])

    def read_drain(self) -> list:
        out = []
        with perf.T("readback"):
            while self.core.in_flight() > 0:
                out.append(np.ascontiguousarray(
                    self.core.frame_acquire(0, True, False)[::-1, :, :3]))
        return out

    def yuv_from_rgb(self, rgb: np.ndarray):
        """A CPU-composited frame (top-down RGB) as the writer's yuv420p bytes
        (rows bottom-up). The ring is empty when this is called, so the frame
        is returned at once; the arithmetic is the same as the GPU kernel's."""
        from ..gl import rgb_to_yuv420p
        return rgb_to_yuv420p(np.ascontiguousarray(rgb[::-1]))

    def read_rgb(self) -> np.ndarray:
        """Synchronous (h, w, 3) uint8, top-down: dump-frames and tests."""
        if self._target != 0:              # a renderer that draws into its own texture
            self._replay()
            self.core.aux_commit()
            return np.ascontiguousarray(self.core.tex_read(self._target)[::-1, :, :3])
        if self.core.in_flight() > 0:
            raise RuntimeError("read_rgb with frames still in flight: drain first")
        self._replay()
        self.core.frame_commit(False)
        frame = None
        while self.core.in_flight() > 0:
            frame = self.core.frame_acquire(0, True, False)
        return np.ascontiguousarray(frame[::-1, :, :3])

    def recycle_frame(self, frame) -> None:
        return None                      # frames are views of the ring: nothing to pool

    def release(self) -> None:
        if self._own_core:
            self.core.close()
