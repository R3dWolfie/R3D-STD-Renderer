"""Mania HEM analytic primitives, adapted to Standard's GL and Metal APIs.

Pure commands are shared by both backends. Programs, quad and original icon
textures are cached; per-frame work only updates uniforms/command records.
The GLSL authority is R3D Mania gpu/hit_error_painter.py (AGPL-3.0).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import numpy as np
from PIL import Image

from .gl import Sprite
from .hit_error_meter import (BLUE, CENTRE_DARK_BLUE, CENTRE_MARKER_SIZE,
    CHEVRON_STROKE_WIDTH, COLUMN_SIZE, EDGE_FADE_SIZE, HIT_RESULT_COLOURS,
    ICON_SIZE, MARKER_MAX_CONCURRENT, TICK_THICKNESS, hit_error_meter_geometry,
    hit_error_offset_position, hit_error_tick_state, hit_window_bands)


@dataclass(frozen=True)
class MeterShape:
    rect: tuple
    colour: tuple
    mode: int = 0
    a: tuple = (0, 0)
    b: tuple = (0, 0)
    c: tuple = (0, 0)
    radius: float = 0
    fade: float = 0
    additive: bool = False


@dataclass(frozen=True)
class MeterIcon:
    name: str
    centre: tuple
    scale: float
    opacity: float


def meter_commands(scene, width, height, opacity=1.0):
    bands = hit_window_bands(scene.hit_error_windows)
    if not bands or opacity <= 0:
        return ()
    g = hit_error_meter_geometry(width, height)
    scale = g.scale
    commands = []
    def point(p):
        return g.component_x+p[0], g.component_top+p[1]
    def stroke(points, thickness, colour, additive=False):
        radius = thickness/2
        xs, ys = zip(*points)
        margin = radius+1
        commands.append(MeterShape(
            (min(xs)-margin, min(ys)-margin, max(xs)-min(xs)+2*margin,
             max(ys)-min(ys)+2*margin),
            (*colour[:3], colour[3]*opacity), 2 if len(points)==3 else 0,
            points[0], points[1], points[-1], radius, additive=additive))
    for band in reversed(bands):
        x,y,w,h = g.band_rect(band.relative_length)
        commands.append(MeterShape((g.component_x+x,g.component_top+y,w,h),
            (*band.colour,opacity),mode=1,
            fade=EDGE_FADE_SIZE*scale if band is bands[-1] else 0))
    centre = point((g.axis_centre,g.cross_centre))
    stroke((centre,centre),CENTRE_MARKER_SIZE*scale,(*BLUE,1))
    events = [e for e in scene.hit_error_events if e.judgment in HIT_RESULT_COLOURS][-MARKER_MAX_CONCURRENT:]
    for event in events:
        state = hit_error_tick_state(event.age_ms)
        if state.alpha <= 0 or state.width_fraction <= 0:
            continue
        position = hit_error_offset_position(event.offset_ms,bands[-1].window_ms)
        stroke(tuple(point(p) for p in g.marker_segment(position,state.width_fraction)),
               TICK_THICKNESS*scale,(*HIT_RESULT_COLOURS[event.judgment],state.alpha),True)
    stroke((centre,centre),CENTRE_MARKER_SIZE/2*scale,(*CENTRE_DARK_BLUE,1))
    for name, centre in (('hare',g.early_icon_centre),('tortoise',g.late_icon_centre)):
        commands.append(MeterIcon(name,point(centre),scale,opacity))
    if scene.hit_error_chevron_position is not None:
        stroke(tuple(point(p) for p in g.chevron_points(scene.hit_error_chevron_position)),
               CHEVRON_STROKE_WIDTH*scale,(1,1,1,.95))
    return tuple(commands)


# GLSL strings are the unchanged Mania authority (screen is top-down).
_VERTEX = """
#version 330
in vec2 in_pos;
uniform vec2 screen;
uniform vec4 rect;
out vec2 point;
void main() {
    point = rect.xy + in_pos * rect.zw;
    gl_Position = vec4(point.x / screen.x * 2.0 - 1.0,
                       1.0 - point.y / screen.y * 2.0, 0.0, 1.0);
}
"""

_FRAGMENT = """
#version 330
in vec2 point;
out vec4 frag;
uniform vec4 colour;
uniform vec4 rect;
uniform vec2 a;
uniform vec2 b;
uniform vec2 c;
uniform float radius;
uniform float fade;
uniform int mode;
float segmentDistance(vec2 p, vec2 start, vec2 end) {
    vec2 delta = end - start;
    float t = clamp(dot(p - start, delta) / max(dot(delta, delta), 0.00001), 0.0, 1.0);
    return length(p - start - delta * t);
}
void main() {
    float alpha = 1.0;
    if (mode == 1) {
        // No texture sampling/slices: exact linear fade in render pixels.
        if (fade > 0.0)
            alpha = clamp(min(point.x - rect.x, rect.x + rect.z - point.x) / fade, 0.0, 1.0);
    } else {
        float distance = segmentDistance(point, a, b);
        if (mode == 2) distance = min(distance, segmentDistance(point, b, c));
        alpha = 1.0 - smoothstep(radius - 0.5, radius + 0.5, distance);
    }
    frag = vec4(colour.rgb, colour.a * alpha);
}
"""

_METAL = """
#include <metal_stdlib>
using namespace metal;
struct HemU { float4 screen_mode; float4 rect; float4 colour; float4 ab; float4 crf; };
struct HemV { float4 pos [[position]]; float2 point; };
vertex HemV hem_vs(uint vid [[vertex_id]], constant HemU& u [[buffer(2)]]) {
    const float2 corners[6] = {float2(0,0),float2(1,0),float2(1,1),float2(0,0),float2(1,1),float2(0,1)};
    float2 p = u.rect.xy + corners[vid] * u.rect.zw;
    HemV o; o.point=p;
    o.pos=float4(p.x/u.screen_mode.x*2-1, (1-p.y/u.screen_mode.y*2)*u.screen_mode.w, 0, 1);
    return o;
}
float hem_distance(float2 p, float2 start, float2 end) {
    float2 delta=end-start;
    float t=clamp(dot(p-start,delta)/max(dot(delta,delta),0.00001f),0.0f,1.0f);
    return length(p-start-delta*t);
}
fragment float4 hem_fs(HemV in [[stage_in]], constant HemU& u [[buffer(2)]]) {
    float alpha=1;
    int mode=int(u.screen_mode.z);
    if (mode==1) {
        if (u.crf.w>0) alpha=clamp(min(in.point.x-u.rect.x,u.rect.x+u.rect.z-in.point.x)/u.crf.w,0.0f,1.0f);
    } else {
        float d=hem_distance(in.point,u.ab.xy,u.ab.zw);
        if (mode==2) d=min(d,hem_distance(in.point,u.ab.zw,u.crf.xy));
        alpha=1-smoothstep(u.crf.z-0.5f,u.crf.z+0.5f,d);
    }
    return float4(u.colour.rgb,u.colour.a*alpha);
}
"""


def metal_uniforms(command, width, height):
    """Five float4s; the exact shared Metal ABI, testable without macOS."""
    # Standard's Metal core stores render targets bottom-up, like GL.
    return np.array((width,height,command.mode,-1,*command.rect,*command.colour,
                     *command.a,*command.b,*command.c,command.radius,command.fade),dtype='f4')


class HitErrorMeterPainter:
    def __init__(self, renderer):
        self.renderer = renderer
        self.metal = hasattr(renderer,'core')
        self.icons = {}
        root=Path(__file__).resolve().parents[1]/'assets/hud/hit-error-meter'
        for name in ('hare','tortoise'):
            with Image.open(root/(name+'.png')) as im:
                rgba=np.array(im.convert('RGBA'))
            key='hem_'+name
            renderer.upload_texture(key,rgba,clamp=True,mipmaps=False)
            self.icons[name]=(key,rgba.shape[1],rgba.shape[0])
        if self.metal:
            # Keep Metal imports lazy; importing the native core on Linux fails.
            from .metal import core as mc
            self.pipelines = {
                False:renderer.core.pipe_create(_METAL,'hem_vs','hem_fs',mc.RGBA8,mc.BLEND_ALPHA,safe_math=True),
                True:renderer.core.pipe_create(_METAL,'hem_vs','hem_fs',mc.RGBA8,mc.BLEND_ADD,safe_math=True)}
        else:
            gl=renderer.ctx
            self.program=gl.program(vertex_shader=_VERTEX,fragment_shader=_FRAGMENT)
            self.buffer=gl.buffer(np.array([(0,0),(1,0),(1,1),(0,0),(1,1),(0,1)],dtype='f4').tobytes())
            self.vao=gl.simple_vertex_array(self.program,self.buffer,'in_pos')
            self.program['screen'].value=(renderer.width,renderer.height)

    def draw(self, scene, opacity=1):
        import moderngl
        r=self.renderer
        try:
            for command in meter_commands(scene,r.width,r.height,opacity):
                if isinstance(command,MeterIcon):
                    key,w,h=self.icons[command.name]
                    fit=min(ICON_SIZE*command.scale/w,COLUMN_SIZE*command.scale/h)
                    r.draw([Sprite(*command.centre,w*fit,h*fit,key,(1,1,1,command.opacity))])
                elif self.metal:
                    uniform=metal_uniforms(command,r.width,r.height)
                    pipe=self.pipelines[command.additive]
                    r.add_scene(lambda u=uniform,p=pipe:r.core.draw(p,6,uniforms=u))
                else:
                    r.ctx.blend_func=(moderngl.SRC_ALPHA,moderngl.ONE if command.additive else moderngl.ONE_MINUS_SRC_ALPHA)
                    for name,value in (('rect',command.rect),('colour',command.colour),
                        ('mode',command.mode),('a',command.a),('b',command.b),('c',command.c),
                        ('radius',command.radius),('fade',command.fade)):
                        self.program[name].value=value
                    self.vao.render(moderngl.TRIANGLES)
        finally:
            if not self.metal:
                r.ctx.blend_func=(moderngl.SRC_ALPHA,moderngl.ONE_MINUS_SRC_ALPHA)

    def release(self):
        if not self.metal:
            self.vao.release();self.buffer.release();self.program.release()
        # Native Metal pipelines are owned and freed by Core.close().
