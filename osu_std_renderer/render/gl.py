"""Minimal moderngl sprite batch — adapted from the production catch
renderer (osu-catch/osu_catch_renderer/gl.py), context creation split into
context.py. Draws textured/solid quads with straight-alpha blending in
painter's order plus an additive pass (hit explosions / glow), then reads
back tightly-packed RGB24 for the ffmpeg pipe.

Batched draw (perf-optimize): all sprite parameters ride per-vertex
attributes in ONE dynamic VBO built per draw() call, and consecutive
sprites sharing a texture collapse into a single indexed glDrawElements —
~444 draw calls/frame became ~40 (med-map profile). The vertex/fragment
math is expression-identical to the per-sprite uniform path it replaces
(same rotate/NDC lines, `flat` colour so no interpolation), so the raster
output is bit-identical; the per-frame blake2b frame-stream hash proved it
on the med benchmark map.

The mania v2 gpu/ package (atlas, texture arrays, PBO readback) remains
the performance end-state; texture-atlas packing is deliberately NOT done
here — mipmapped LINEAR sampling at atlas edges cannot be proven
pixel-identical against per-texture repeat wrapping.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import moderngl
except Exception as e:  # noqa: BLE001
    raise RuntimeError("moderngl is required for the std renderer") from e

from . import perf
from .context import create_context

_VERT = """
#version 330
in vec2 in_pos;      // unit quad corner [-0.5,0.5]
in vec2 in_uv;
in vec2 in_center;   // sprite center in px (origin top-left)
in vec2 in_size;     // sprite w,h in px
in float in_rot;     // radians
in vec4 in_color;
in vec2 in_uv_off;   // texture sub-rect (spinner-metre reveal)
in vec2 in_uv_scale;
uniform vec2 u_screen;   // (w, h) in px
out vec2 v_uv;
flat out vec4 v_color;
void main() {
    vec2 p = in_pos * in_size;
    float c = cos(in_rot), s = sin(in_rot);
    p = vec2(p.x * c - p.y * s, p.x * s + p.y * c);
    vec2 px = in_center + p;
    vec2 ndc = vec2(px.x / u_screen.x * 2.0 - 1.0,
                    1.0 - px.y / u_screen.y * 2.0);
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = in_uv * in_uv_scale + in_uv_off;
    v_color = in_color;
}
"""

_FRAG = """
#version 330
in vec2 v_uv;
flat in vec4 v_color;
uniform sampler2D u_tex;
out vec4 f_color;
void main() {
    vec4 t = texture(u_tex, v_uv);
    f_color = t * v_color;
}
"""

# floats per vertex: in_pos(2) in_uv(2) center(2) size(2) rot(1) color(4)
# uv_off(2) uv_scale(2)
_VERT_FLOATS = 17
_SPRITE_BYTES = 4 * _VERT_FLOATS * 4          # 4 corners × 17 f4


@dataclass
class Sprite:
    """A single textured/coloured quad to draw this frame (back-to-front)."""
    x: float                 # screen px, center
    y: float                 # screen px, center
    w: float
    h: float
    texture_key: str | None = None      # None = solid colour quad
    color: tuple[float, float, float, float] = (1, 1, 1, 1)
    rotation: float = 0.0
    additive: bool = False   # additive blend (glow / hit explosion)
    # texture sub-rect (uv offset/scale) — the spinner-metre bottom-up
    # reveal draws only the bottom fraction of its texture
    uv_off: tuple[float, float] = (0.0, 0.0)
    uv_scale: tuple[float, float] = (1.0, 1.0)


class SpriteRenderer:
    def __init__(self, width: int, height: int,
                 ctx: "moderngl.Context | None" = None):
        self.width = width
        self.height = height
        self.ctx = ctx or create_context()
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)

        self.prog = self.ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        self.prog["u_screen"].value = (float(width), float(height))
        self.prog["u_tex"].value = 0

        # unit-quad corners + uv, replicated per sprite in _draw (v grows
        # downward with screen y — same corner order the old TRIANGLE_STRIP
        # used; the index pattern below re-emits its two triangles)
        self._corners = np.array([
            [-0.5, -0.5, 0.0, 0.0],
            [ 0.5, -0.5, 1.0, 0.0],
            [-0.5,  0.5, 0.0, 1.0],
            [ 0.5,  0.5, 1.0, 1.0],
        ], dtype="f4")

        self._capacity = 0
        self.vbo: "moderngl.Buffer | None" = None
        self._ibo: "moderngl.Buffer | None" = None
        self.vao: "moderngl.VertexArray | None" = None
        self._ensure_capacity(2048)

        # texture-backed colour attachment (was a renderbuffer): the bloom
        # post-pass samples the scene, and fbo.read() works the same
        self.color_tex = self.ctx.texture((width, height), 4)
        self.color_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.fbo = self.ctx.framebuffer(color_attachments=[self.color_tex])
        self._textures: dict[str, "moderngl.Texture"] = {}
        self._nomip_keys: set[str] = set()
        self._white = self._make_texture_rgba(np.full((1, 1, 4), 255, dtype="u1"))
        # optional per-sprite post-transform (Sprite -> Sprite), applied to
        # every sprite in draw(). The fail animation installs this to drop
        # the frozen playfield's objects off-screen; None = identity.
        self.post_xform = None

    def _ensure_capacity(self, n_sprites: int) -> None:
        """Size the dynamic VBO + static index buffer for n_sprites quads."""
        if n_sprites <= self._capacity:
            return
        cap = max(n_sprites, self._capacity * 2, 2048)
        if self.vao is not None:
            self.vao.release()
        if self._ibo is not None:
            self._ibo.release()
        if self.vbo is None:
            self.vbo = self.ctx.buffer(reserve=cap * _SPRITE_BYTES,
                                       dynamic=True)
        else:
            self.vbo.orphan(cap * _SPRITE_BYTES)
        # two triangles per quad: (0,1,2) + (2,1,3) — the same coverage the
        # old strip produced for corners v0..v3
        idx = (np.arange(cap, dtype="u4")[:, None] * 4
               + np.array([0, 1, 2, 2, 1, 3], dtype="u4")[None, :])
        self._ibo = self.ctx.buffer(np.ascontiguousarray(idx))
        self.vao = self.ctx.vertex_array(
            self.prog,
            [(self.vbo, "2f 2f 2f 2f 1f 4f 2f 2f",
              "in_pos", "in_uv", "in_center", "in_size", "in_rot",
              "in_color", "in_uv_off", "in_uv_scale")],
            index_buffer=self._ibo, index_element_size=4,
        )
        self._capacity = cap

    # --- texture management ---------------------------------------------------

    def upload_texture(self, key: str, rgba: np.ndarray,
                       clamp: bool = False, mipmaps: bool = True) -> None:
        """rgba: HxWx4 uint8 array (top-left origin). Re-uploading a key
        releases the previous texture (the HUD hp bar re-uploads per
        frame — without the release that's a VRAM leak). clamp=True sets
        clamp-to-edge wrapping (the flashlight overlay samples uv beyond
        [0,1] and needs the edge texel, not a repeat). mipmaps=False skips
        the mipmap build + uses plain LINEAR — for a texture drawn at ~1:1
        every frame (the SSAA base blit) building a full mip chain each
        frame is pure waste."""
        with perf.T("tex_upload"):
            self._upload_texture(key, rgba, clamp=clamp, mipmaps=mipmaps)

    def _upload_texture(self, key: str, rgba: np.ndarray,
                        clamp: bool = False, mipmaps: bool = True) -> None:
        if rgba.dtype != np.uint8:
            rgba = rgba.astype("u1")
        if rgba.shape[2] == 3:
            a = np.full(rgba.shape[:2] + (1,), 255, dtype="u1")
            rgba = np.concatenate([rgba, a], axis=2)
        old = self._textures.get(key)
        tex = self._make_texture_rgba(rgba, mipmaps=mipmaps)
        if clamp:
            tex.repeat_x = False
            tex.repeat_y = False
        self._textures[key] = tex
        if old is not None:
            try:
                old.release()
            except Exception:  # noqa: BLE001 - context may be tearing down
                pass

    def write_texture(self, key: str, rgba: np.ndarray,
                      clamp: bool = False) -> None:
        """Per-frame texture update: same-size re-writes go through
        glTexSubImage2D on the EXISTING texture object — no allocation, no
        release, no mipmap chain (plain LINEAR). Only for textures drawn
        at 1:1 where the mip chain is never sampled (the HUD hp bars);
        the first call (or a size change) allocates a LINEAR no-mip
        texture."""
        with perf.T("tex_upload"):
            if rgba.dtype != np.uint8:
                rgba = rgba.astype("u1")
            if rgba.shape[2] == 3:
                a = np.full(rgba.shape[:2] + (1,), 255, dtype="u1")
                rgba = np.concatenate([rgba, a], axis=2)
            h, w = rgba.shape[:2]
            tex = self._textures.get(key)
            if key in self._nomip_keys and tex is not None \
                    and tex.size == (w, h):
                tex.write(rgba)
                return
            self._upload_texture(key, rgba, clamp=clamp, mipmaps=False)
            self._nomip_keys.add(key)

    def has_texture(self, key: str) -> bool:
        return key in self._textures

    def release_texture(self, key: str) -> None:
        """Free a cached texture by key (storyboard LRU eviction). No-op if
        the key is absent."""
        tex = self._textures.pop(key, None)
        self._nomip_keys.discard(key)
        if tex is not None:
            try:
                tex.release()
            except Exception:  # noqa: BLE001 - context may be tearing down
                pass

    def _make_texture_rgba(self, rgba: np.ndarray,
                           mipmaps: bool = True) -> "moderngl.Texture":
        h, w = rgba.shape[:2]
        tex = self.ctx.texture((w, h), 4, rgba.tobytes())
        if mipmaps:
            tex.build_mipmaps()
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        else:
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return tex

    # --- drawing --------------------------------------------------------------

    def begin(self, clear=(0.0, 0.0, 0.0)) -> None:
        self.fbo.use()
        self.ctx.clear(*clear)

    def draw(self, sprites: list[Sprite]) -> None:
        with perf.T("gl_sprites_draw"):
            perf.count("sprites", len(sprites))
            self._draw(sprites)

    def _draw(self, sprites: list[Sprite]) -> None:
        if self.post_xform is not None:
            sprites = [self.post_xform(sp) for sp in sprites]
        if not sprites:
            return
        # painter's order per pass: every non-additive sprite in order,
        # THEN every additive sprite in order (the exact two-phase order
        # the per-sprite loop produced)
        n_norm = 0
        if any(sp.additive for sp in sprites):
            normal = [sp for sp in sprites if not sp.additive]
            n_norm = len(normal)
            ordered = normal + [sp for sp in sprites if sp.additive]
        else:
            ordered = sprites
            n_norm = len(ordered)
        n = len(ordered)
        self._ensure_capacity(n)

        params = np.array(
            [(sp.x, sp.y, sp.w, sp.h, sp.rotation,
              sp.color[0], sp.color[1], sp.color[2], sp.color[3],
              sp.uv_off[0], sp.uv_off[1], sp.uv_scale[0], sp.uv_scale[1])
             for sp in ordered], dtype="f4")
        verts = np.empty((n, 4, _VERT_FLOATS), dtype="f4")
        verts[:, :, 0:4] = self._corners
        verts[:, :, 4:] = params[:, None, :]
        self.vbo.orphan()
        self.vbo.write(verts)

        textures = self._textures
        white = self._white
        texs = [textures.get(sp.texture_key, white) if sp.texture_key
                else white for sp in ordered]

        self._run_pass(texs, 0, n_norm)
        if n_norm < n:
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE)
            self._run_pass(texs, n_norm, n)
            self.ctx.blend_func = (moderngl.SRC_ALPHA,
                                   moderngl.ONE_MINUS_SRC_ALPHA)

    def _run_pass(self, texs, start: int, end: int) -> None:
        """Draw quads [start, end) grouping consecutive same-texture runs
        into single indexed draws (primitive order == list order, so the
        painter's-algorithm blending is unchanged)."""
        i = start
        render = self.vao.render
        while i < end:
            tex = texs[i]
            j = i + 1
            while j < end and texs[j] is tex:
                j += 1
            tex.use(location=0)
            render(moderngl.TRIANGLES, vertices=(j - i) * 6, first=i * 6)
            perf.count("draw_calls")
            i = j

    def read_rgb(self) -> np.ndarray:
        """HxWx3 uint8, top-left origin (ready for ffmpeg rgb24)."""
        with perf.T("readback"):
            data = self.fbo.read(components=3, alignment=1)
            arr = np.frombuffer(data, dtype="u1").reshape(
                (self.height, self.width, 3))
            return np.flipud(arr)  # moderngl reads bottom-left origin

    def release(self) -> None:
        try:
            self.ctx.release()
        except Exception:  # noqa: BLE001
            pass
