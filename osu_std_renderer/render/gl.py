"""Minimal moderngl sprite batch — adapted from the production catch
renderer (osu-catch/osu_catch_renderer/gl.py), context creation split into
context.py. Draws textured/solid quads with straight-alpha blending in
painter's order plus an additive pass (hit explosions / glow), then reads
back tightly-packed RGB24 for the ffmpeg pipe.

The mania v2 gpu/ package (atlas, texture arrays, instancing, PBO readback)
is the performance end-state; this batch is the correctness baseline the
std draw phase starts from — same trajectory catch/taiko took.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import moderngl
except Exception as e:  # noqa: BLE001
    raise RuntimeError("moderngl is required for the std renderer") from e

from .context import create_context

_VERT = """
#version 330
in vec2 in_pos;      // unit quad corner [-0.5,0.5]
in vec2 in_uv;
uniform vec2 u_screen;   // (w, h) in px
uniform vec2 u_center;   // sprite center in px (origin top-left)
uniform vec2 u_size;     // sprite w,h in px
uniform float u_rot;     // radians
uniform vec2 u_uv_off;   // texture sub-rect (spinner-metre reveal)
uniform vec2 u_uv_scale;
out vec2 v_uv;
void main() {
    vec2 p = in_pos * u_size;
    float c = cos(u_rot), s = sin(u_rot);
    p = vec2(p.x * c - p.y * s, p.x * s + p.y * c);
    vec2 px = u_center + p;
    vec2 ndc = vec2(px.x / u_screen.x * 2.0 - 1.0,
                    1.0 - px.y / u_screen.y * 2.0);
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = in_uv * u_uv_scale + u_uv_off;
}
"""

_FRAG = """
#version 330
in vec2 v_uv;
uniform sampler2D u_tex;
uniform vec4 u_color;
out vec4 f_color;
void main() {
    vec4 t = texture(u_tex, v_uv);
    f_color = t * u_color;
}
"""


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
        # unit quad centered at origin; v grows downward with screen y
        quad = np.array([
            -0.5, -0.5, 0.0, 0.0,
             0.5, -0.5, 1.0, 0.0,
            -0.5,  0.5, 0.0, 1.0,
             0.5,  0.5, 1.0, 1.0,
        ], dtype="f4")
        self.vbo = self.ctx.buffer(quad.tobytes())
        self.vao = self.ctx.vertex_array(
            self.prog, [(self.vbo, "2f 2f", "in_pos", "in_uv")],
        )
        self.prog["u_screen"].value = (float(width), float(height))

        # texture-backed colour attachment (was a renderbuffer): the bloom
        # post-pass samples the scene, and fbo.read() works the same
        self.color_tex = self.ctx.texture((width, height), 4)
        self.color_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.fbo = self.ctx.framebuffer(color_attachments=[self.color_tex])
        self._textures: dict[str, "moderngl.Texture"] = {}
        self._white = self._make_texture_rgba(np.full((1, 1, 4), 255, dtype="u1"))
        # optional per-sprite post-transform (Sprite -> Sprite), applied to
        # every sprite in draw(). The fail animation installs this to drop
        # the frozen playfield's objects off-screen; None = identity.
        self.post_xform = None

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

    def has_texture(self, key: str) -> bool:
        return key in self._textures

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
        if self.post_xform is not None:
            sprites = [self.post_xform(sp) for sp in sprites]
        add = []
        for sp in sprites:
            if sp.additive:
                add.append(sp)
            else:
                self._draw_one(sp)
        if add:
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE)
            for sp in add:
                self._draw_one(sp)
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)

    def _draw_one(self, sp: Sprite) -> None:
        tex = self._textures.get(sp.texture_key) if sp.texture_key else self._white
        if tex is None:
            tex = self._white
        tex.use(location=0)
        self.prog["u_tex"].value = 0
        self.prog["u_color"].value = sp.color
        self.prog["u_center"].value = (sp.x, sp.y)
        self.prog["u_size"].value = (sp.w, sp.h)
        self.prog["u_rot"].value = sp.rotation
        self.prog["u_uv_off"].value = sp.uv_off
        self.prog["u_uv_scale"].value = sp.uv_scale
        self.vao.render(moderngl.TRIANGLE_STRIP)

    def read_rgb(self) -> np.ndarray:
        """HxWx3 uint8, top-left origin (ready for ffmpeg rgb24)."""
        data = self.fbo.read(components=3, alignment=1)
        arr = np.frombuffer(data, dtype="u1").reshape((self.height, self.width, 3))
        return np.flipud(arr)  # moderngl reads bottom-left origin

    def release(self) -> None:
        try:
            self.ctx.release()
        except Exception:  # noqa: BLE001
            pass
