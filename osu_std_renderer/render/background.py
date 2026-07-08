"""Map background + dim envelope — RENDER_PLAN.md §4.10 Playfield.Background
(§5.3 draw order: the background layer draws first, under everything).

BACKGROUND IMAGE
  The `[Events]` background filename (beatmap.bg) is resolved
  case-insensitively in the map folder (beatmap.get_related_file — the
  FileMap pattern), loaded to RGBA and drawn as one sprite behind the
  scene, aspect-FILLED to the frame (cover: scale = max ratio, centered
  crop — the overflow is clipped by the viewport). No bg file / failed
  decode → fail-soft to the previous dark-void clear (a WARNING, never an
  error).

DIM ENVELOPE (§4.10 Dim{Intro, Normal, Breaks}; R3D preset keys
bg_dim_intro/game/breaks, 0-100%)
  Dim is applied by tinting the bg sprite grey (1-dim) — objects/HUD above
  keep full brightness, matching the reference's background-only dim.
  The envelope is a piecewise level with smoothstep glides (~500 ms,
  the reference's dim glider feel):

    intro dim   until the FIRST object's approach begins (startTime -
                Preempt); the glide INTO gameplay dim completes exactly
                at that moment so the first approach is never half-dimmed
    normal dim  through gameplay
    breaks dim  during `[Events]` breaks (beatmap.pauses): glide starts at
                the break start; the glide BACK completes by
                min(break end, next object's approach start) — "back for
                the next object"
    breaks too short to fit both glides are skipped (dim stays at normal)

  Pure math (DimEnvelope) so tests need no GL.

VIDEO HOOK — load_video_background() is a STUB this phase: §4.10
LoadVideos / the R3D `load_video` preset key is accepted upstream but
backgrounds are still images only (danser handles video maps until the
video phase lands here).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

GLIDE_MS = 500.0     # dim glide length (~danser's dim glider feel)


def smoothstep(p: float) -> float:
    """3p² - 2p³ on [0,1] (clamped) — the glide ease."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return p * p * (3.0 - 2.0 * p)


def cover_size(frame_w: float, frame_h: float,
               img_w: float, img_h: float) -> tuple[float, float]:
    """Aspect-fill (CSS `cover`) draw size: the image scaled by the LARGER
    frame/image ratio so it covers the whole frame; centered by the caller,
    overflow clipped by the viewport."""
    s = max(frame_w / img_w, frame_h / img_h)
    return (img_w * s, img_h * s)


@dataclass(frozen=True)
class _Glide:
    t0: float
    t1: float
    v0: float
    v1: float


class DimEnvelope:
    """Piecewise dim level: holds between glides, smoothsteps inside them.

    glides — (t0, t1, target_level) time-sorted; a glide overlapping the
    previous one (or zero/negative length) is DROPPED, keeping the level
    it would have started from (the envelope never jumps).
    """

    def __init__(self, initial: float,
                 glides: list[tuple[float, float, float]] = ()):
        self.initial = float(initial)
        self._glides: list[_Glide] = []
        level = self.initial
        last_end = -float("inf")
        for t0, t1, target in glides:
            if t0 < last_end or t1 <= t0:
                continue  # overlap/degenerate → dropped, level unchanged
            self._glides.append(_Glide(t0, t1, level, float(target)))
            level = float(target)
            last_end = t1
        self._starts = [g.t0 for g in self._glides]

    def level(self, t: float) -> float:
        """Dim in [0,1] at map time t (ms)."""
        idx = bisect.bisect_right(self._starts, t) - 1
        if idx < 0:
            return self.initial
        g = self._glides[idx]
        if t >= g.t1:
            return g.v1
        return g.v0 + (g.v1 - g.v0) * smoothstep((t - g.t0) / (g.t1 - g.t0))


def build_dim_envelope(intro: float, normal: float, breaks: float,
                       object_starts: list[float], preempt: float,
                       pauses, glide_ms: float = GLIDE_MS) -> DimEnvelope:
    """§4.10 semantics over the map: see the module docstring. Levels are
    fractions 0..1 (callers divide the 0-100 preset values)."""
    starts = sorted(object_starts)
    if not starts:
        return DimEnvelope(normal)
    glides: list[tuple[float, float, float]] = []
    first_spawn = starts[0] - preempt
    glides.append((first_spawn - glide_ms, first_spawn, normal))
    last_end = first_spawn
    for p in sorted(pauses, key=lambda p: p.start_time):
        nxt = next((s for s in starts if s >= p.end_time), None)
        anchor = p.end_time if nxt is None else min(p.end_time, nxt - preempt)
        # both glides must fit: enter [start, start+g], return [anchor-g, anchor]
        if p.start_time < last_end or anchor - glide_ms < p.start_time + glide_ms:
            continue  # too short / out of order → dim stays at normal
        glides.append((p.start_time, p.start_time + glide_ms, breaks))
        glides.append((anchor - glide_ms, anchor, normal))
        last_end = anchor
    return DimEnvelope(intro, glides)


def load_background(path: Path) -> np.ndarray | None:
    """Decode a background image → HxWx4 uint8, or None (fail-soft: a
    corrupt/unreadable file must never kill a render)."""
    try:
        with Image.open(path) as img:
            return np.asarray(img.convert("RGBA"), dtype=np.uint8).copy()
    except Exception:  # noqa: BLE001 — any decode failure → dark void
        return None


def load_video_background(*_args, **_kwargs) -> None:
    """§4.10 LoadVideos hook — STUB. Background video is a later phase;
    the R3D `load_video` preset key is accepted and ignored until then."""
    return None
