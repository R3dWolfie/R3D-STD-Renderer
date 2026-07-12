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
import random

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


# --- replay Smoke (key bit 16) --------------------------------------------------------
# Port of osu!(lazer) osu.Game.Rulesets.Osu/Skinning/SmokeSegment.cs +
# UI/SmokeContainer.cs (MIT). While the player holds Smoke, dabs are dropped
# along the cursor path; each fades over seconds and the whole stroke burns
# away after release. Constants are lazer's verbatim.
SMOKE_KEY_BIT = 16             # replay key bitfield bit (== replay.KEY_SMOKE)

SMOKE_INITIAL_FADE_MS = 4000.0   # initial_fade_out_duration
SMOKE_REFADE_SPEED    = 3.0      # re_fade_in_speed
SMOKE_REFADE_MS       = 50.0     # re_fade_in_duration
SMOKE_FINAL_SPEED     = 2.0      # final_fade_out_speed
SMOKE_FINAL_FADE_MS   = 8000.0   # final_fade_out_duration
SMOKE_INITIAL_ALPHA   = 0.6      # initial_alpha
SMOKE_REFADE_ALPHA    = 1.0      # re_fade_in_alpha

SMOKE_SCALE_MS      = 1200.0     # scale_duration (out-quint 0.65 -> 1.0)
SMOKE_INITIAL_SCALE = 0.65       # initial_scale
SMOKE_ROT_MS        = 500.0      # rotation_duration (out-quint settle)
SMOKE_MAX_ROT       = 0.25       # max_rotation, radians

# lazer default cursor-smoke texture is 64px @2x -> 32 logical px; SmokeSegment
# width = DisplayWidth * 0.165 -> ~5.28 osu!px. Used when no skin cursor-smoke.
SMOKE_DEFAULT_WIDTH_OSU = 32.0 * 0.165        # ~5.28
# safety cap: a pathological all-map smoke hold can't blow memory up
SMOKE_MAX_POINTS_PER_SEG = 8192


class SmokeSeg:
    """One held-Smoke stroke, resampled to dabs (SmokeContainer segment).

    pts   : [(x_osu, y_osu, spawn_ms, angle_rad, settle_rad)] in draw order
    times : [spawn_ms] parallel to pts, non-decreasing (bisect index)
    start_ms/end_ms : press / release map-ms (end == last frame if never
                      released before the replay ended)
    kill_ms : lazer LifetimeEnd — after this the stroke is fully gone."""
    __slots__ = ("start_ms", "end_ms", "kill_ms", "pts", "times")

    def __init__(self, start_ms, end_ms, kill_ms, pts, times):
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.kill_ms = kill_ms
        self.pts = pts
        self.times = times


class _SegBuilder:
    """Reproduces SmokeSegment.StartDrawing/AddPosition point placement:
    one dab per `interval` osu!px of cursor travel, the first at the press
    position. Positions are interpolated along each replay-frame segment;
    every dab added on one frame shares that frame's timestamp (lazer stamps
    them all with Time.Current). Angles/settle come from a per-SEGMENT RNG
    (seeded by segment index) so re-renders are byte-reproducible."""

    def __init__(self, frame, interval: float, rng: "random.Random"):
        self.interval = interval
        self.rng = rng
        self.start_ms = float(frame.time_ms)
        self.pts: list = []
        self.last_pos = None
        self.total = interval          # StartDrawing: totalDistance = pointInterval
        self._add_position(float(frame.x), float(frame.y), self.start_ms)

    def _add_position(self, x: float, y: float, t: float) -> None:
        if self.last_pos is None:
            self.last_pos = (x, y)
        lx, ly = self.last_pos
        dx, dy = x - lx, y - ly
        delta = math.hypot(dx, dy)
        self.total += delta
        count = int(self.total / self.interval)
        if count > 0:
            count = min(count, SMOKE_MAX_POINTS_PER_SEG - len(self.pts))
        if count > 0:
            if delta > 1e-12:
                nx, ny = dx / delta, dy / delta
            else:
                nx, ny = 0.0, 0.0
            start_off = self.interval - (self.total - delta)
            px, py = start_off * nx + lx, start_off * ny + ly
            ix, iy = nx * self.interval, ny * self.interval
            self.total %= self.interval
            # lazer's monotonic guard: only append if the batch is in order
            if not self.pts or self.pts[-1][2] <= t:
                for _ in range(count):
                    angle = self.rng.uniform(0.0, 2.0 * math.pi)
                    settle = SMOKE_MAX_ROT * (self.rng.random() * 2.0 - 1.0)
                    self.pts.append((px, py, t, angle, settle))
                    px += ix
                    py += iy
        self.last_pos = (x, y)

    def feed(self, frame) -> None:
        self._add_position(float(frame.x), float(frame.y), float(frame.time_ms))

    def finish(self, end_ms: float) -> SmokeSeg:
        # SmokeSegment.FinishDrawing: LifetimeEnd = end + final_fade_out_duration
        #   + trunc/re_fade_in_speed + trunc/final_fade_out_speed
        trunc = min(SMOKE_INITIAL_FADE_MS, end_ms - self.start_ms)
        kill_ms = (end_ms + SMOKE_FINAL_FADE_MS
                   + trunc / SMOKE_REFADE_SPEED
                   + trunc / SMOKE_FINAL_SPEED)
        times = [p[2] for p in self.pts]
        return SmokeSeg(self.start_ms, end_ms, kill_ms, self.pts, times)


def smoke_segments(frames, interval_osu: float) -> list:
    """[SmokeSeg] — one per maximal run of frames holding the Smoke bit.

    Returns [] when the replay never pressed Smoke (the gate that keeps every
    non-smoke render byte-identical: the scene's draw self-gates on this being
    non-empty). `interval_osu` is pointInterval = width * 7/8 in osu!px."""
    if not frames:
        return []
    interval = max(float(interval_osu), 1e-3)
    segs: list = []
    cur = None
    seg_index = 0
    prev = 0
    for f in frames:
        keys = int(f.keys)
        held = keys & SMOKE_KEY_BIT
        was = prev & SMOKE_KEY_BIT
        if held and not was:
            cur = _SegBuilder(f, interval, random.Random(seg_index))
            seg_index += 1
        elif held and cur is not None:
            cur.feed(f)
        elif (not held) and was and cur is not None:
            segs.append(cur.finish(float(f.time_ms)))
            cur = None
        prev = keys
    if cur is not None:
        segs.append(cur.finish(float(frames[-1].time_ms)))
    return segs


def smoke_point_alpha(pt_ms: float, t: float,
                      start_ms: float, end_ms: float) -> float:
    """Alpha 0..1 of one dab at map-time t — exact port of
    SmokeDrawNode.ApplyState + PointColour (SmokeSegment.cs). Three phases:
    linear initial fade-out (0.6 -> 0 over 4 s while held); on release a
    re-brighten wave to 1.0 (out-quint, 50 ms) sweeping at speed 3; then a
    final fade to 0 (^5 ease, up to 8 s) sweeping at speed 2."""
    trunc = min(SMOKE_INITIAL_FADE_MS, end_ms - start_ms)
    fvpt = end_ms - trunc                      # firstVisiblePointTimeAfterSmokeEnded
    initial_fo_time = min(t, end_ms)
    refade_time = t - trunc - fvpt * (1.0 - 1.0 / SMOKE_REFADE_SPEED)
    final_fo_time = t - trunc - fvpt * (1.0 - 1.0 / SMOKE_FINAL_SPEED)

    time_final = final_fo_time - pt_ms / SMOKE_FINAL_SPEED
    if time_final > 0.0 and pt_ms >= fvpt:
        frac = _clamp01(time_final / SMOKE_FINAL_FADE_MS) ** 5
        return (1.0 - frac) * SMOKE_REFADE_ALPHA

    a = 1.0                                    # Color4.White default (A = 1):
    # a dab sampled at its exact spawn instant stays at 1.0 (bright leading
    # edge at the cursor); one tick later the initial-fade branch drops it to
    # ~initial_alpha (0.6) and fades from there.
    time_init = initial_fo_time - pt_ms
    if time_init > 0.0:
        frac = _clamp01(time_init / SMOKE_INITIAL_FADE_MS)
        a = (1.0 - frac) * SMOKE_INITIAL_ALPHA
    if pt_ms > fvpt:
        time_re = refade_time - pt_ms / SMOKE_REFADE_SPEED
        if time_re > 0.0:
            frac = 1.0 - (1.0 - _clamp01(time_re / SMOKE_REFADE_MS)) ** 5
            a = frac * (SMOKE_REFADE_ALPHA - a) + a
    return a
