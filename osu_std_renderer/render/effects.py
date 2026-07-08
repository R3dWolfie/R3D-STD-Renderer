"""Effect lifecycle math — the settings-surface phase's pure functions
(no GL; scene.py draws from these, tests import them directly):

  * bg triangles      the osu-style triangle field drifting upward
                      (deterministic per seed — same render every time)
  * intro logo        the R3D "R" tile splash alpha/scale envelope
                      (house branding per the owner directive; the tile
                      is baked procedurally in textures.bake_logo_tile —
                      versus_splash.py's red-tile-white-R look)
  * seizure warning   danser-style dark card envelope at render start
  * fade to black     §4.10 FadeOutTime envelope after the last object
  * warning arrows    stable-style flashing arrows before gameplay
                      resumes after a break (§4.6 ShowWarningArrows)
  * cursor ripples    §4.8 CursorRipples — expanding ring per press EDGE
  * cursor rainbow    §4.8 rainbow — hue-cycled cursor/trail tint
"""
from __future__ import annotations

import colorsys
import math

# --- bg triangles ---------------------------------------------------------------
TRI_COUNT = 24                 # triangles alive at any moment
TRI_MIN_SIZE = 0.06            # of frame height
TRI_MAX_SIZE = 0.22
TRI_MIN_SPEED = 0.018          # frame-heights per second (slow drift)
TRI_MAX_SPEED = 0.05
TRI_ALPHA = 0.085              # subtle by design
TRI_SHADE_MIN = 0.55           # greyscale tint range (osu triangles read
TRI_SHADE_MAX = 1.0            # as brightness variations of the bg hue)

# --- intro logo -----------------------------------------------------------------
LOGO_FADE_IN_MS = 300.0
LOGO_FADE_OUT_MS = 500.0       # ends exactly as gameplay's first approach
LOGO_MIN_WINDOW_MS = 700.0     # not enough intro to read the logo → skip
LOGO_MAX_ALPHA = 0.92
LOGO_UI_SIZE = 220.0           # tile edge in the 1080-space

# --- seizure warning ------------------------------------------------------------
SEIZURE_DURATION_S = 5.0       # the preset exposes no duration — 5 s default
SEIZURE_FADE_MS = 500.0        # card fade into the scene at the end

# --- fade out -------------------------------------------------------------------
# (envelope only — the length comes from settings.fade_out_time × speed)

# --- warning arrows -------------------------------------------------------------
WARN_WINDOW_MS = 1000.0        # arrows show this long before play resumes
WARN_BLINK_MS = 200.0          # full on/off blink period (stable-ish)
WARN_MIN_BREAK_MS = 1500.0     # don't warn on blink-and-you-miss-it breaks

# --- cursor ripples -------------------------------------------------------------
RIPPLE_LIFE_MS = 350.0
RIPPLE_START_SCALE = 0.35      # of the full ripple diameter
RIPPLE_ALPHA = 0.5

# --- cursor rainbow -------------------------------------------------------------
RAINBOW_CYCLE_MS = 3000.0      # full hue cycle (danser-ish pace)
RAINBOW_SATURATION = 0.65      # keep it tint-like, not neon


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# --- triangles --------------------------------------------------------------------

def triangle_field(seed: int = 0, count: int = TRI_COUNT,
                   ) -> list[tuple[float, float, float, float, float]]:
    """The per-render triangle population: [(x_frac, size_frac, speed_fps,
    shade, phase)] — deterministic from `seed` (golden-ratio hashing, the
    miss_fall_transform pattern) so the same replay renders identically."""
    out = []
    for i in range(count):
        h1 = ((seed + i) * 0.618033988749895) % 1.0
        h2 = ((seed + i) * 0.754877666246693 + 0.17) % 1.0
        h3 = ((seed + i) * 0.569840290998053 + 0.41) % 1.0
        h4 = ((seed + i) * 0.882537117212201 + 0.77) % 1.0
        size = TRI_MIN_SIZE + (TRI_MAX_SIZE - TRI_MIN_SIZE) * h2 * h2
        speed = TRI_MIN_SPEED + (TRI_MAX_SPEED - TRI_MIN_SPEED) * h3
        shade = TRI_SHADE_MIN + (TRI_SHADE_MAX - TRI_SHADE_MIN) * h4
        out.append((h1, size, speed, shade, (h1 * 7.13 + h4) % 1.0))
    return out


def triangle_y(phase: float, speed: float, size: float, t_ms: float) -> float:
    """Centre y_frac of one triangle at time t: drifts UP (decreasing y)
    at `speed` frame-heights/s, wrapping from fully-above-the-top (-size)
    back under the bottom edge (1+size) — the osu menu-triangles motion."""
    span = 1.0 + 2.0 * size
    return ((phase * span - (t_ms / 1000.0) * speed) % span) - size


def triangle_states(field, t_ms: float,
                    ) -> list[tuple[float, float, float, float, float]]:
    """[(x_frac, y_frac, size_frac, shade, alpha)] at time t. Fractions
    are of frame height for y/size, of frame width for x; y_frac is the
    CENTRE and sits slightly outside [0,1] while a triangle straddles an
    edge (the caller draws it anyway — the viewport clips)."""
    return [(x, triangle_y(phase, speed, size, t_ms), size, shade,
             TRI_ALPHA)
            for x, size, speed, shade, phase in field]


# --- intro logo --------------------------------------------------------------------

def logo_alpha(t: float, t_start: float, gameplay_in: float,
               ) -> float | None:
    """The intro splash alpha at map time t, or None when the logo phase
    is inactive. Window = [t_start, gameplay_in] (render start → the first
    object's approach start): fade in over LOGO_FADE_IN_MS, hold, fade out
    over LOGO_FADE_OUT_MS ENDING at gameplay_in. Windows too short to read
    (< LOGO_MIN_WINDOW_MS) show nothing."""
    if gameplay_in - t_start < LOGO_MIN_WINDOW_MS:
        return None
    if t < t_start or t >= gameplay_in:
        return None
    a_in = _clamp01((t - t_start) / LOGO_FADE_IN_MS)
    a_out = _clamp01((gameplay_in - t) / LOGO_FADE_OUT_MS)
    a = LOGO_MAX_ALPHA * min(a_in, a_out)
    return a if a > 0.0 else None


def logo_scale(t: float, t_start: float) -> float:
    """Gentle settle: 1.06 → 1.0 over the first 600 ms (quad-out)."""
    p = _clamp01((t - t_start) / 600.0)
    ease = 1.0 - (1.0 - p) * (1.0 - p)
    return 1.06 - 0.06 * ease


# --- seizure warning ---------------------------------------------------------------

def seizure_alpha(t: float, t0: float,
                  duration_ms: float = SEIZURE_DURATION_S * 1000.0,
                  ) -> float | None:
    """Card alpha at map time t, or None when over: opaque through the
    card, fading out over the last SEIZURE_FADE_MS into the scene."""
    if t < t0:
        return None
    end = t0 + duration_ms
    if t >= end:
        return None
    return _clamp01((end - t) / SEIZURE_FADE_MS)


# --- fade out ----------------------------------------------------------------------

def fade_to_black_alpha(t: float, fade_start: float,
                        fade_len_ms: float) -> float:
    """§4.10 FadeOutTime: 0 before fade_start, ramping to 1 (full black)
    over fade_len_ms, held at 1 after (the results screen draws on top)."""
    if fade_len_ms <= 0.0 or t <= fade_start:
        return 0.0
    return _clamp01((t - fade_start) / fade_len_ms)


# --- warning arrows ------------------------------------------------------------------

def break_resume_anchors(pauses, object_starts: list[float],
                         preempt: float) -> list[float]:
    """The moment gameplay 'resumes' per break — the dim envelope's
    anchor: min(break end, next object's approach start). Breaks shorter
    than WARN_MIN_BREAK_MS are skipped (nothing to warn about)."""
    starts = sorted(object_starts)
    out: list[float] = []
    for p in sorted(pauses, key=lambda p: p.start_time):
        if p.end_time - p.start_time < WARN_MIN_BREAK_MS:
            continue
        nxt = next((s for s in starts if s >= p.end_time), None)
        anchor = p.end_time if nxt is None else min(p.end_time, nxt - preempt)
        if anchor > p.start_time:
            out.append(anchor)
    return out


def warning_arrow_alpha(t: float, anchors: list[float],
                        window_ms: float = WARN_WINDOW_MS,
                        blink_ms: float = WARN_BLINK_MS) -> float:
    """Arrow alpha at time t: inside the window before an anchor the
    arrows BLINK (square wave, blink_ms period, soft 20 %-edge ramps so
    30 fps renders don't strobe unevenly); 0 outside every window."""
    for a in anchors:
        if a - window_ms <= t < a:
            phase = ((t - (a - window_ms)) % blink_ms) / blink_ms
            # on for the first half of each period, eased edges
            if phase >= 0.5:
                return 0.0
            edge = 0.1
            up = _clamp01(phase / edge)
            down = _clamp01((0.5 - phase) / edge)
            return min(up, down)
    return 0.0


# --- cursor ripples -------------------------------------------------------------------

def ripple_events(frames) -> list[tuple[float, float, float]]:
    """[(t_ms, x_osu, y_osu)] — one ripple per press EDGE of any of the 4
    key channels (a frame whose key mask gains any bit)."""
    out: list[tuple[float, float, float]] = []
    prev = 0
    for f in frames:
        k = f.keys & 0xF
        if k & ~prev:
            out.append((float(f.time_ms), float(f.x), float(f.y)))
        prev = k
    return out


def ripple_states(events, times, t: float,
                  life_ms: float = RIPPLE_LIFE_MS,
                  ) -> list[tuple[float, float, float, float]]:
    """[(x_osu, y_osu, scale01, alpha)] of the live ripples at t —
    `times` is the precomputed [t for t, _, _ in events] bisect index.
    scale01 grows RIPPLE_START_SCALE→1 (quad-out), alpha fades to 0."""
    import bisect
    hi = bisect.bisect_right(times, t)
    lo = bisect.bisect_left(times, t - life_ms, 0, hi)
    out = []
    for i in range(lo, hi):
        te, x, y = events[i]
        p = _clamp01((t - te) / life_ms)
        ease = 1.0 - (1.0 - p) * (1.0 - p)
        out.append((x, y,
                    RIPPLE_START_SCALE + (1.0 - RIPPLE_START_SCALE) * ease,
                    RIPPLE_ALPHA * (1.0 - p)))
    return out


# --- cursor rainbow -------------------------------------------------------------------

def rainbow_rgb(t: float, cycle_ms: float = RAINBOW_CYCLE_MS,
                ) -> tuple[float, float, float]:
    """Hue-cycled tint at time t (§4.8 EnableRainbow, danser-ish pace)."""
    hue = (t / cycle_ms) % 1.0
    return colorsys.hsv_to_rgb(hue, RAINBOW_SATURATION, 1.0)
