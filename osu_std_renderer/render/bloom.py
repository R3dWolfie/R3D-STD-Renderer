"""Post-process bloom — §4.10 Playfield.Bloom (R3D `bloom` /
`bloom_to_beat`), the classic 3-stage chain done cheaply:

  1. BRIGHT PASS  the scene texture downsampled to quarter resolution,
                  keeping only energy above BLOOM_THRESHOLD (soft knee);
  2. BLUR         one separable 9-tap gaussian (H then V) at quarter res —
                  quarter-res blur ≈ a 4× wider kernel at full res for
                  ~1/16 the fragment work;
  3. RECOMBINE    the blurred brights drawn back over the scene FBO with
                  ADDITIVE blending, scaled by `strength`.

The scene applies it AFTER gameplay + cursor and BEFORE the HUD (owner
call: HUD text/numbers stay crisp — danser blooms its whole frame, lazer
blooms nothing; splitting the difference keeps readability).

`bloom_to_beat` pulses `strength` on the red-line beats (the scene feeds
beat_phase through beat_strength()).

Perf: 3 fullscreen-ish passes at quarter res + one additive quad. On the
2070S @1080p60 this measured ≈2-4 % of the frame budget (see the phase
report); still OFF by default.
"""
from __future__ import annotations

import struct

try:
    import moderngl
except Exception as e:  # noqa: BLE001
    raise RuntimeError("moderngl is required for the std renderer") from e

BLOOM_THRESHOLD = 0.58        # bright-pass knee (post-dim scenes are dark)
BLOOM_BASE_STRENGTH = 0.8     # danser Bloom.Power default
BLOOM_BEAT_ADDITION = 0.4     # danser Bloom.BloomBeatAddition default
DOWNSCALE = 4

_QUAD_VERT = """
#version 330
in vec2 in_pos;               // NDC corner
out vec2 v_uv;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_pos * 0.5 + 0.5;
}
"""

_BRIGHT_FRAG = """
#version 330
uniform sampler2D u_tex;
uniform float u_threshold;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec3 c = texture(u_tex, v_uv).rgb;
    float lum = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float k = clamp((lum - u_threshold) / max(1.0 - u_threshold, 1e-4),
                    0.0, 1.0);
    f_color = vec4(c * k * k, 1.0);   // soft knee
}
"""

_BLUR_FRAG = """
#version 330
uniform sampler2D u_tex;
uniform vec2 u_dir;           // (1/w, 0) or (0, 1/h)
in vec2 v_uv;
out vec4 f_color;
void main() {
    // 9-tap gaussian, sigma≈1.8 (weights normalized)
    float w[5] = float[](0.2270, 0.1946, 0.1216, 0.0540, 0.0162);
    vec3 acc = texture(u_tex, v_uv).rgb * w[0];
    for (int i = 1; i < 5; i++) {
        vec2 off = u_dir * float(i);
        acc += texture(u_tex, v_uv + off).rgb * w[i];
        acc += texture(u_tex, v_uv - off).rgb * w[i];
    }
    f_color = vec4(acc, 1.0);
}
"""

_COMBINE_FRAG = """
#version 330
uniform sampler2D u_tex;
uniform float u_strength;
in vec2 v_uv;
out vec4 f_color;
void main() {
    f_color = vec4(texture(u_tex, v_uv).rgb * u_strength, 1.0);
}
"""


def beat_strength(beat_phase: float, to_beat: bool,
                  base: float = BLOOM_BASE_STRENGTH,
                  addition: float = BLOOM_BEAT_ADDITION) -> float:
    """Bloom strength at `beat_phase` ∈ [0,1): the danser semantics —
    Power, plus BloomBeatAddition at the beat decaying to 0 by the next
    one (quad decay) when `bloom_to_beat` is on."""
    if not to_beat:
        return base
    p = max(0.0, min(1.0, beat_phase))
    return base + addition * (1.0 - p) * (1.0 - p)


class BloomPass:
    """One reusable bloom chain per render (FBOs allocated once)."""

    def __init__(self, ctx: "moderngl.Context", width: int, height: int):
        self.ctx = ctx
        self.w, self.h = width, height
        self.sw = max(width // DOWNSCALE, 1)
        self.sh = max(height // DOWNSCALE, 1)

        self._tex_a = ctx.texture((self.sw, self.sh), 4)
        self._tex_b = ctx.texture((self.sw, self.sh), 4)
        for t in (self._tex_a, self._tex_b):
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = t.repeat_y = False   # clamp — no edge wrap bleed
        self._fbo_a = ctx.framebuffer(color_attachments=[self._tex_a])
        self._fbo_b = ctx.framebuffer(color_attachments=[self._tex_b])

        quad = b"".join(struct.pack("2f", *p)
                        for p in ((-1, -1), (1, -1), (-1, 1), (1, 1)))
        self._vbo = ctx.buffer(quad)
        self._progs = {}
        self._vaos = {}
        for name, frag in (("bright", _BRIGHT_FRAG), ("blur", _BLUR_FRAG),
                           ("combine", _COMBINE_FRAG)):
            prog = ctx.program(vertex_shader=_QUAD_VERT,
                               fragment_shader=frag)
            self._progs[name] = prog
            self._vaos[name] = ctx.vertex_array(
                prog, [(self._vbo, "2f", "in_pos")])
        self._progs["bright"]["u_threshold"].value = BLOOM_THRESHOLD

    def apply(self, scene_tex: "moderngl.Texture",
              target_fbo: "moderngl.Framebuffer",
              strength: float) -> None:
        """Bright-pass + blur `scene_tex`, then add it onto `target_fbo`
        (which is normally the FBO scene_tex is attached to — the sampling
        passes render into the chain FBOs first, so there is never a
        simultaneous read+write of the same attachment)."""
        if strength <= 0.0:
            return
        ctx = self.ctx
        ctx.disable(moderngl.BLEND)

        self._fbo_a.use()
        ctx.viewport = (0, 0, self.sw, self.sh)
        scene_tex.use(location=0)
        self._progs["bright"]["u_tex"].value = 0
        self._vaos["bright"].render(moderngl.TRIANGLE_STRIP)

        self._fbo_b.use()
        self._tex_a.use(location=0)
        self._progs["blur"]["u_tex"].value = 0
        self._progs["blur"]["u_dir"].value = (1.0 / self.sw, 0.0)
        self._vaos["blur"].render(moderngl.TRIANGLE_STRIP)

        self._fbo_a.use()
        self._tex_b.use(location=0)
        self._progs["blur"]["u_dir"].value = (0.0, 1.0 / self.sh)
        self._vaos["blur"].render(moderngl.TRIANGLE_STRIP)

        target_fbo.use()
        ctx.viewport = (0, 0, self.w, self.h)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.ONE, moderngl.ONE)   # pure additive
        self._tex_a.use(location=0)
        self._progs["combine"]["u_tex"].value = 0
        self._progs["combine"]["u_strength"].value = strength
        self._vaos["combine"].render(moderngl.TRIANGLE_STRIP)
        ctx.blend_func = (moderngl.SRC_ALPHA,
                          moderngl.ONE_MINUS_SRC_ALPHA)

    def release(self) -> None:
        for obj in (self._fbo_a, self._fbo_b, self._tex_a, self._tex_b,
                    self._vbo, *self._progs.values()):
            try:
                obj.release()
            except Exception:  # noqa: BLE001
                pass
