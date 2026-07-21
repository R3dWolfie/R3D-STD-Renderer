"""osu_std_renderer/merge.py — showdown!mrgd: N std replays on ONE shared field.
Autoplay field + N per-player-tinted cursors (skin cursor + sparse trail);
optional live left ScoreV3 leaderboard. Video-only for now."""
from __future__ import annotations
import colorsys
import os
import sys
from pathlib import Path

import numpy as np


def _colors(n: int) -> list[tuple[float, float, float]]:
    out = []
    for i in range(n):
        h = (i / max(1, n) + 0.11) % 1.0
        out.append(colorsys.hsv_to_rgb(h, 0.82, 1.0))
    return out


# R3D always rides a red cursor — matched on player name, wins over any pick.
_R3D_NAMES = {"r3d", "r3dwolfie"}
_R3D_RED = colorsys.hsv_to_rgb(0.0, 0.85, 1.0)


def _parse_hex(s):
    """'#RRGGBB' / 'RRGGBB' -> (r,g,b) floats, or None."""
    s = (s or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None


def _clamp_legible(rgb):
    """Floor value + saturation so a dark/washed pick still reads on the dark
    field (the cursor whitening multiplies, so a muddy colour disappears)."""
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    return colorsys.hsv_to_rgb(h, max(s, 0.45), max(v, 0.85))


def _hue_dist(a, b):
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def _resolve_player_colors(players, user_hex):
    """Per-player cursor colour: R3D-red (locked) > user pick > auto palette,
    then de-collide by rotating any hue that lands too close to an already-
    placed one. R3D's red is placed first and never moves — everyone else
    nudges around it, so two identical picks can never share a cursor."""
    n = len(players)
    auto = _colors(n)
    user_hex = list(user_hex or [])
    desired, locked = [], set()
    for i in range(n):
        name = (getattr(players[i][1], "player_name", "") or "").strip().lower()
        if name in _R3D_NAMES:
            desired.append(_R3D_RED); locked.add(i)
        else:
            uh = _parse_hex(user_hex[i]) if i < len(user_hex) else None
            desired.append(_clamp_legible(uh) if uh else auto[i])
    # minimum hue separation on the wheel (< 1/n so N distinct hues always fit)
    min_sep = min(0.09, 0.7 / max(1, n))
    placed_h, out_hsv = [], [None] * n
    for i in list(locked) + [j for j in range(n) if j not in locked]:
        h, s, v = colorsys.rgb_to_hsv(*desired[i])
        if i not in locked:
            for _ in range(720):
                if all(_hue_dist(h, ph) >= min_sep for ph in placed_h):
                    break
                h = (h + 1.0 / 360.0) % 1.0
        out_hsv[i] = (h, s, v); placed_h.append(h)
    return [colorsys.hsv_to_rgb(*out_hsv[i]) for i in range(n)]


def _whiten_skin_cursor(skin_dir):
    """Cursor/trail/middle -> luminance in RGB (keep alpha) so they tint cleanly
    to ANY player colour (a pre-coloured cyan/yellow would multiply to black for
    opposite hues). Keeps the soft glow SHAPE, drops the hue."""
    from PIL import Image
    d = Path(skin_dir)
    for stem in ("cursor", "cursortrail", "cursormiddle",
                 "cursor@2x", "cursortrail@2x", "cursormiddle@2x"):
        p = d / f"{stem}.png"
        if not p.exists():
            continue
        im = Image.open(p).convert("RGBA")
        lum = im.convert("L")
        Image.merge("RGBA", (lum, lum, lum, im.split()[3])).save(p)


def _font(mono: bool, size: int):
    from PIL import ImageFont
    cands = ([
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ] if mono else [
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ])
    for c in cands:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.truetype(
        "/home/foof/r3drender/osu-std/osu_std_renderer/assets/fonts/Nunito[wght].ttf", size)


# leaderboard palette (Red's spec): score+acc take the GRADE colour, the
# 100/50/0 counts are fixed green/yellow/red, the name stays the cursor colour.
_GRADE_COL = {
    "SS": (0.35, 0.92, 0.98),   # cyan
    "S":  (0.98, 0.86, 0.32),   # yellow
    "A":  (0.42, 0.92, 0.48),   # green
    "B":  (0.42, 0.62, 0.99),   # blue
    "C":  (0.74, 0.47, 0.97),   # purple
    "D":  (0.97, 0.40, 0.40),   # red / fail
}
_COL_100 = (0.47, 0.90, 0.50)   # green
_COL_50 = (0.98, 0.84, 0.36)    # yellow
_COL_MISS = (0.97, 0.42, 0.42)  # red


class MergeBoard:
    """Live left leaderboard. entries: [(name, color, HudData, end_ms)].
    Rows slide to their sorted slot (exp-smoothed ~0.2s); dead players (replay
    ended) dim; grade uses the skin ranking image, else a letter."""

    def __init__(self, entries, height, skin_dirs=(), width=460, top_n=40):
        self.entries = entries
        self.W, self.H, self.N = width, height, top_n
        rows = min(len(entries), top_n)
        self.hh = 30                                    # "showdown!mrgd" header strip
        self.rh = max(16, min(26, (height - 20 - self.hh) // max(1, rows)))
        self.fmono = _font(True, int(self.rh * 0.72))   # legacy fallback
        # Type = the site's OWN fonts (Red 2026-07-16, option B): JetBrains
        # Mono for figures/rank/credit — the exact face the site uses for
        # numbers + `//` labels — and Sora for names (the site's body face).
        # TTFs converted from the site's woff2; falls back to _font().
        _FDIR = "/home/foof/r3drender/mania_ordr/mania_ordr/static/fonts"
        _JB6 = f"{_FDIR}/JetBrainsMono-600.ttf"
        _JB4 = f"{_FDIR}/JetBrainsMono-400.ttf"
        _SORA6 = f"{_FDIR}/Sora-600.ttf"

        def _pf(path, size, mono=False):
            from PIL import ImageFont
            try:
                return ImageFont.truetype(path, max(6, int(size)))
            except Exception:
                return _font(mono, max(6, int(size)))
        self.f_score = _pf(_JB6, self.rh * 0.56, mono=True)
        self.f_rank = _pf(_JB6, self.rh * 0.50, mono=True)
        self.fname = _pf(_SORA6, self.rh * 0.56)
        self.f_acc = _pf(_JB4, self.rh * 0.40, mono=True)
        self.fcredit = _pf(_JB6, self.hh * 0.36, mono=True)
        self._ypos = {}
        self._last_t = None
        self._grades = self._load_grades(skin_dirs, int(self.rh * 0.95))
        # final-result score per player → tiebreak when current scores tie
        # (bias the eventual winner higher, per Red)
        self._final = {e[0]: int(e[2].score_at(1e12)) for e in entries}

    def _load_grades(self, skin_dirs, h):
        """grade string (grade_at → SS/S/A/B/C/D) -> skin ranking badge image.
        grade_at says 'SS' but the file is ranking-X(-small); map SS→X via the
        renderer's GRADE_RANKING_ELEMENT letter, trying @2x + BOTH cases (osu
        skins name small badges lowercase, full badges upper). Falls through
        skin → default skin (which ships the full ranking set)."""
        from PIL import Image
        letter = {"SS": "X", "S": "S", "A": "A", "B": "B", "C": "C", "D": "D"}
        out = {}
        for g, L in letter.items():
            names = []
            for base in (f"{L}-small", f"{L.lower()}-small", L, L.lower()):
                names += [f"ranking-{base}@2x", f"ranking-{base}"]
            for sd in skin_dirs:
                if not sd:
                    continue
                for stem in names:
                    p = Path(sd) / f"{stem}.png"
                    if p.exists():
                        im = Image.open(p).convert("RGBA")
                        r = h / max(1, im.height)
                        out[g] = im.resize((max(1, int(im.width * r)), h))
                        break
                if g in out:
                    break
        return out

    def render(self, t):
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        rows = []
        for name, col, hud, end_ms in self.entries:
            sc = hud.score_at(t); ac = hud.acc_at(t) * 100.0
            dead = end_ms is not None and t > end_ms + 500.0
            rows.append((sc, ac, name, col, dead))
        rows.sort(key=lambda r: (-r[0], -self._final.get(r[2], 0)))  # tie → final result
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        # Snappier settle so rows that swap slots don't linger overlapping.
        a = 1.0 if dt <= 0 else min(1.0, 1.0 - 2.718281828 ** (-dt / 22.0))

        # ── "Rail" leaderboard (Red 2026-07-16) — glassy gradient card, a
        # Gaussian bloom pass behind the accuracy meters + leader, Archivo
        # Black figures / Inter names. Credit line replaces showdown!mrgd. ──
        rh = self.rh
        gap = max(3, int(rh * 0.20))
        barh = rh - gap
        rad = max(4, barh // 2)
        n = min(len(rows), self.N)
        y0 = self.hh + 5
        L, R = 9, self.W - 9
        inner_w = R - L

        # smoothed layout once (shared by the bloom + sharp passes)
        laid = []
        for i, (sc, ac, name, col, dead) in enumerate(rows[:self.N]):
            target = y0 + i * rh
            cur = self._ypos.get(name, target)
            cur += (target - cur) * a
            self._ypos[name] = cur
            laid.append((i, int(round(cur)), sc, ac, name, col, dead, i == 0))
        # Bar length = score RELATIVE to the leader (Red 2026-07-16): the gap
        # you see is the real score deficit, not accuracy. Leader = full.
        max_sc = max(1.0, float(laid[0][2])) if laid else 1.0
        panel_h = min(self.H - 1, y0 + n * rh + 6)

        # ── glassy panel: vertical gradient, rounded, masked ──
        f = np.linspace(0.0, 1.0, panel_h)[:, None]
        top = np.array([23.0, 24.0, 31.0]); bot = np.array([9.0, 10.0, 14.0])
        pg = np.empty((panel_h, self.W, 4), dtype=np.uint8)
        pg[:, :, :3] = (top * (1 - f) + bot * f)[:, None, :].astype(np.uint8)
        pg[:, :, 3] = 212
        pmask = Image.new("L", (self.W, panel_h), 0)
        ImageDraw.Draw(pmask).rounded_rectangle(
            [1, 1, self.W - 2, panel_h - 2], radius=13, fill=255)
        img.paste(Image.fromarray(pg, "RGBA"), (0, 0), pmask)

        # ── bloom pass: emissive meters + leader ring, blurred, added under ──
        glow = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for (i, y, sc, ac, name, col, dead, lead) in laid:
            if dead:
                continue
            rgb = tuple(int(max(0.0, min(1.0, v)) * 255) for v in col)
            aw = int(inner_w * max(0.0, min(1.0, sc / max_sc)))
            gd.rounded_rectangle([L, y, L + max(8, aw), y + barh], radius=rad,
                                 fill=(*rgb, 135))
            if lead:
                gd.rounded_rectangle([L - 1, y - 1, R + 1, y + barh + 1],
                                     radius=rad + 1, outline=(242, 84, 100, 215), width=3)
        img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(7)))
        d = ImageDraw.Draw(img)

        # crisp border + top sheen over the bloom
        d.rounded_rectangle([1, 1, self.W - 2, panel_h - 2], radius=13,
                            outline=(255, 255, 255, 40), width=1)
        d.line([15, 3, self.W - 15, 3], fill=(255, 255, 255, 34), width=1)

        # ── credit header ──
        d.text((16, 7), "MADE BY ", font=self.fcredit, fill=(151, 153, 169, 255))
        _wmb = self.fcredit.getlength("MADE BY ")
        d.text((16 + _wmb, 7), "renderer.r3dwolfie.com", font=self.fcredit,
               fill=(243, 97, 109, 255))
        d.line([16, self.hh, self.W - 16, self.hh], fill=(255, 255, 255, 24), width=1)

        # ── sharp rows ──
        for (i, y, sc, ac, name, col, dead, lead) in laid:
            ra = 0.42 if dead else 1.0
            rgb = tuple(int(max(0.0, min(1.0, v)) * 255) for v in col)
            cy = y + barh / 2.0

            bar = (37, 17, 24, int(188 * ra)) if lead else (20, 21, 27, int(148 * ra))
            d.rounded_rectangle([L, y, R, y + barh], radius=rad, fill=bar)

            aw = int(inner_w * max(0.0, min(1.0, sc / max_sc)))
            if aw > 4:
                lay = Image.new("RGBA", (self.W, barh + 1), (0, 0, 0, 0))
                ld = ImageDraw.Draw(lay)
                ld.rounded_rectangle([L, 0, L + aw, barh], radius=rad, fill=(*rgb, int(72 * ra)))
                ld.rectangle([L + aw - 3, 2, L + aw, barh - 2], fill=(*rgb, int(245 * ra)))
                img.alpha_composite(lay, (0, y))

            if lead:
                d.rounded_rectangle([L, y, R, y + barh], radius=rad,
                                    outline=(246, 104, 116, int(238 * ra)), width=1)

            # rank — Archivo Black, gold leader
            rcol = (252, 209, 110) if lead else (150, 151, 165)
            d.text((L + 11, cy), str(i + 1), font=self.f_rank,
                   fill=(*rcol, int(255 * ra)), anchor="mm")

            # name (Inter) + small acc, with a soft shadow for legibility
            nx = L + 26
            nm = name[:16]
            d.text((nx + 1, cy + 1), nm, font=self.fname,
                   fill=(0, 0, 0, int(115 * ra)), anchor="lm")
            d.text((nx, cy), nm, font=self.fname,
                   fill=(243, 244, 249, int(255 * ra)), anchor="lm")
            accs = "100%" if ac >= 100.0 else f"{ac:.2f}%"
            d.text((nx + self.fname.getlength(nm) + 9, cy + 1), accs, font=self.f_acc,
                   fill=(156, 158, 172, int(235 * ra)), anchor="lm")

            # score — Archivo Black, right-aligned, comma-grouped
            scr = f"{int(sc):,}"
            scol = (255, 255, 255) if lead else (227, 228, 235)
            d.text((R - 4, cy + 1), scr, font=self.f_score,
                   fill=(0, 0, 0, int(120 * ra)), anchor="rm")
            d.text((R - 5, cy), scr, font=self.f_score,
                   fill=(*scol, int(255 * ra)), anchor="rm")
        return self.W, self.H, np.asarray(img, dtype=np.uint8)


class _PerfectSim:
    """Duck-typed SimResult for PERFECT (robotic autoplay) hitsounds: every
    object judged hit at its EXACT time, every slider part (head/tick/repeat/
    tail) hit, the slide loop tracked across the whole slider. No replay, no
    jitter, no missing notes. collect_hitsound_events only reads a verdict's
    .hit_time / .parts / .tracking and sim.classic, so a SimpleNamespace does."""
    classic = False

    def __init__(self, beatmap):
        from types import SimpleNamespace
        from .ruleset.ruleset import PartOutcome
        from .beatmap.objects import Slider, Spinner
        _kmap = {"tick": "tick", "reverse": "repeat", "last": "tail"}
        self._v = {}
        for obj in beatmap.hit_objects:
            if isinstance(obj, Spinner):
                v = SimpleNamespace(hit_time=obj.end_time, parts=[], tracking=[])
            elif isinstance(obj, Slider):
                st, en = obj.start_time, obj.end_time
                parts = [PartOutcome(time=st, kind="head", pos=(0.0, 0.0), hit=True)]
                for tp in obj.score_points:
                    parts.append(PartOutcome(
                        time=(en if tp.kind == "last" else tp.time),
                        kind=_kmap[tp.kind], pos=(0.0, 0.0), hit=True))
                v = SimpleNamespace(hit_time=st, parts=parts, tracking=[(st, en)])
            else:                                   # hit circle
                v = SimpleNamespace(hit_time=obj.start_time, parts=[], tracking=[])
            self._v[id(obj)] = v

    def verdict_for(self, obj):
        return self._v.get(id(obj))


_BREAKDOWN_SLOW = 0.25          # absolute playback rate inside a breakdown window
_BREAKDOWN_RAMP_MS = 260.0      # ease in/out duration
_BREAKDOWN_TOP_K = 3
_BREAKDOWN_PAD_MS = 900.0
_BREAKDOWN_MERGE_GAP_MS = 1500.0
_BREAKDOWN_MAX_TOTAL_MS = 20000.0


def _build_breakdown_rate(osu_path, speed, first_obj_ms, end_ms):
    """Detect the hardest aim-strain sections (rosu-pp) and return
    (rate_fn, windows). rate_fn(map_t) = `speed` outside windows,
    `_BREAKDOWN_SLOW` inside, smoothstep-eased at the edges. Windows are
    absolute map-time (ms). Returns (None, []) when unavailable."""
    try:
        import rosu_pp_py as _r
        import numpy as _np
        st = _r.Difficulty().strains(_r.Beatmap(path=str(osu_path)))
        aim = _np.asarray(list(st.aim), dtype=float)
        sec = float(getattr(st, "section_len", 400.0))
    except Exception as _e:  # noqa: BLE001
        print(f"  breakdown: strain calc failed: {_e}", file=sys.stderr)
        return None, []
    if aim.size < 3:
        return None, []
    thr = _np.percentile(aim, 86)
    hot = aim >= thr
    runs, i, n = [], 0, len(hot)
    while i < n:
        if hot[i]:
            j = i
            while j < n and hot[j]:
                j += 1
            runs.append((i, j - 1)); i = j
        else:
            i += 1
    if not runs:
        return None, []
    runs.sort(key=lambda r: -aim[r[0]:r[1] + 1].max())
    wins = []
    for (a, b) in runs:
        ws = first_obj_ms + a * sec - _BREAKDOWN_PAD_MS
        we = first_obj_ms + (b + 1) * sec + _BREAKDOWN_PAD_MS
        wins.append([max(0.0, ws), min(end_ms, we), float(aim[a:b + 1].max())])
    wins.sort(key=lambda w: w[0])
    merged = []
    for w in wins:
        if merged and w[0] - merged[-1][1] <= _BREAKDOWN_MERGE_GAP_MS:
            merged[-1][1] = max(merged[-1][1], w[1])
            merged[-1][2] = max(merged[-1][2], w[2])
        else:
            merged.append(list(w))
    merged.sort(key=lambda w: -w[2])
    chosen, total = [], 0.0
    for w in merged:
        d = w[1] - w[0]
        if len(chosen) >= _BREAKDOWN_TOP_K or total + d > _BREAKDOWN_MAX_TOTAL_MS:
            continue
        chosen.append((w[0], w[1])); total += d
    chosen.sort()
    if not chosen:
        return None, []
    ramp, slow = _BREAKDOWN_RAMP_MS, _BREAKDOWN_SLOW

    def _smooth(x):
        x = 0.0 if x < 0 else (1.0 if x > 1 else x)
        return x * x * (3.0 - 2.0 * x)

    def rate_fn(t):
        for (ss, ee) in chosen:
            if ss - ramp <= t <= ee + ramp:
                if t < ss:
                    f = _smooth((t - (ss - ramp)) / ramp)
                elif t > ee:
                    f = _smooth(((ee + ramp) - t) / ramp)
                else:
                    f = 1.0
                return speed * (1.0 - f) + slow * f
        return speed

    return rate_fn, chosen


def render_merge(osr_paths, beatmap_dir, output, *,
                 skin_dir=None, default_skin_dir=None,
                 resolution=(1920, 1080), fps=60, max_seconds=None,
                 show_board=False, truncate=None, hitsounds="perfect",
                 start_seconds=None, breakdown=False, player_colors=None):
    from .replay import parse_replay
    from .beatmap import load_full
    from .cli import find_osu_file
    from .render.gl import SpriteRenderer
    from .render.textures import TextureBank
    from .render.slider_body import SliderBodyRenderer
    from .render.playfield import PlayfieldCamera
    from .render.skin_elements import SkinElements
    from .render.scene import StdScene, ScenePlayer
    from .record.encode import FfmpegPipe, build_ffmpeg_cmd, probe_encoder
    from .record.pipeline import RecordPipeline
    from .skin.skin import Skin

    players = []
    for i, p in enumerate(osr_paths):
        frames, meta = parse_replay(Path(p))
        if truncate and i in truncate:      # debug: simulate an early death
            cut = truncate[i]
            frames = [f for f in frames if f.time_ms <= cut]
        players.append((frames, meta))
        print(f"  {meta.player_name:16} frames={len(frames):5} acc={meta.accuracy:.2f}",
              file=sys.stderr)
    md5s = {m.beatmap_md5 for _, m in players}
    assert len(md5s) == 1, f"replays span multiple maps: {md5s}"
    # A merge only ever combines players sharing ONE gameplay-mod signature
    # (the bot roster enforces this). The shared FIELD renders with the group's
    # uniform gameplay mods ONLY; ALL cosmetic mods (HD/FL/NF/SD/PF/SO) are
    # stripped so we never draw N per-player HD-fades / flashlights on one field.
    # Per-player SCORING keeps each player's OWN full mods (via their meta below).
    GAMEPLAY_MASK = 850    # EZ|HR|DT|HT|NC (2|16|64|256|512) — gameplay-mod signature
    field_mods = players[0][1].mods & GAMEPLAY_MASK
    if field_mods & 512:            # Nightcore -> Double Time (same 1.5x speed)
        field_mods = (field_mods & ~512) | 64

    osu_path = find_osu_file(Path(beatmap_dir), players[0][1].beatmap_md5)
    beatmap = load_full(osu_path, mods=field_mods)
    print(f"  beatmap objects={len(beatmap.hit_objects)} length={beatmap.length}ms",
          file=sys.stderr)

    w, h = resolution
    spr = SpriteRenderer(w, h)
    bank = TextureBank(spr)
    bodies = SliderBodyRenderer(spr.ctx, w, h)
    cam = PlayfieldCamera(w, h)
    skin_elems = None
    skin = None
    if skin_dir is not None:
        # Whiten a COPY, never the user's source skin. Whitening in place
        # permanently greyscales the cursor for every future (single)
        # render that uses the same library skin dir (the bug that turned
        # R3D's FREEDOM DiVE cursor white).
        import tempfile as _tf, shutil as _sh
        _wtmp = _tf.mkdtemp(prefix="mrgd_wskin_")
        _sh.copytree(skin_dir, _wtmp, dirs_exist_ok=True)
        _whiten_skin_cursor(_wtmp)
        skin_dir = _wtmp
        skin = Skin(skin_dir=Path(skin_dir),
                    fallback_dir=Path(default_skin_dir) if default_skin_dir else None)
        skin_elems = SkinElements(skin, spr)
        print(f"  skin: {Path(skin_dir).name} (cursor whitened for per-player tint)",
              file=sys.stderr)

    # background + dim (requester's dim in prod; 80% game dim for the test)
    from .render.background import build_dim_envelope, cover_size, load_background
    bg_key = bg_size = None
    bg_path = beatmap.get_related_file(Path(beatmap_dir), beatmap.bg)
    if bg_path is not None:
        rgba = load_background(bg_path)
        if rgba is not None:
            spr.upload_texture("background", rgba)
            bg_key = "background"
            bg_size = cover_size(w, h, rgba.shape[1], rgba.shape[0])
            print(f"  bg: {beatmap.bg}", file=sys.stderr)
    dim_env = build_dim_envelope(
        0.90, 0.80, 0.35,          # intro / gameplay / BREAK — bg brightens on breaks
        [o.get_start_time() for o in beatmap.hit_objects],
        beatmap.diff.preempt, beatmap.pauses)

    cols = _resolve_player_colors(players, player_colors)
    merge_cursors = [(frames, cols[i], frames[-1].time_ms if frames else 0.0)
                     for i, (frames, _m) in enumerate(players)]

    # score every player (lazer ScoreV3) once — powers the board AND the
    # score/acc hitsound-reference modes.
    from .ruleset.ruleset import StdRuleset, JudgmentKind
    from .render.hud import HudData
    sims = [StdRuleset(beatmap, fr, mt).run() for fr, mt in players]
    huds = [HudData(s) for s in sims]

    # Standings sidecar: the final ScoreV3 board written as DATA next to the
    # mp4, so the bot can surface per-player score/acc/grade/mods on /v/.
    try:
        import json as _json
        _rows = sorted(
            ({"name": players[i][1].player_name,
              "mods": int(getattr(players[i][1], "mods", 0) or 0),
              "score": int(huds[i].score_at(1e12)),
              "acc": round(huds[i].acc_at(1e12) * 100.0, 2),
              "grade": huds[i].grade_at(1e12)}
             for i in range(len(players))),
            key=lambda r: -r["score"])
        for _rk, _row in enumerate(_rows, 1):
            _row["rank"] = _rk
        _sc = Path(output)
        _sc = _sc.with_name(_sc.stem + ".standings.json")
        _sc.write_text(_json.dumps(_rows))
        print(f"  standings sidecar -> {_sc.name} ({len(_rows)} players)", file=sys.stderr)
    except Exception as _e:  # noqa: BLE001
        print(f"  standings sidecar failed: {_e}", file=sys.stderr)

    # per-player MISS markers (a colored X at each miss, in the player's colour).
    # showdown!mrgd: the X is placed at THAT PLAYER'S CURSOR position at the miss
    # moment — NOT on the note. When N players miss the same object the note-
    # anchored X'es stacked into one indistinguishable pile; on the cursor each
    # player's choke is visible and placed where THEY actually were.
    # Death gate: a player whose replay ended (fail / quit) produces no more
    # misses. Their frames stop at death, so the ruleset auto-misses EVERY
    # post-death object (frames end at death → window closes unhit) — that was
    # the "dead people still show misses" flood. We drop any miss at/after that
    # player's last frame (their cursor also fades out there, see merge_cursors).
    from .replay.replay import cursor_at
    merge_misses = []
    for i, sim in enumerate(sims):
        _frames = players[i][0]
        _alive_until = _frames[-1].time_ms if _frames else 0.0
        ms = []
        for o in beatmap.hit_objects:
            v = sim.verdict_for(o)
            if v is None or v.kind is not JudgmentKind.MISS:
                continue
            _mt = v.deadline or o.get_start_time()
            if _mt > _alive_until:              # player already dead/gone → skip
                continue
            _cx, _cy, _ = cursor_at(_frames, _mt)   # X shows on the CURSOR
            ms.append((_mt, (_cx, _cy)))
        merge_misses.append((ms, cols[i]))

    # --hitsounds reference: perfect (robotic autoplay — every note on time,
    # DEFAULT), score (top-score play's hits), acc (top-accuracy play's hits),
    # none (silent). collect_hitsound_events walks whatever "sim" we pick.
    ref_sim, ref_name = None, hitsounds
    if hitsounds == "perfect":
        ref_sim, ref_name = _PerfectSim(beatmap), "perfect autoplay"
    elif hitsounds == "score":
        _i = max(range(len(sims)), key=lambda i: int(huds[i].score_at(1e12)))
        ref_sim, ref_name = sims[_i], f"score:{players[_i][1].player_name}"
    elif hitsounds == "acc":
        _i = max(range(len(sims)), key=lambda i: huds[i].acc_at(1e12))
        ref_sim, ref_name = sims[_i], f"acc:{players[_i][1].player_name}"
    # "none" → ref_sim stays None (no hitsound track)

    board = None
    if show_board:
        board = MergeBoard(
            [(players[i][1].player_name, cols[i], huds[i],
              players[i][0][-1].time_ms if players[i][0] else 0.0)
             for i in range(len(players))],
            height=h, skin_dirs=(skin_dir, default_skin_dir))
        print("  scored all players (ScoreV3) + board on", file=sys.stderr)

    # branding: R3D logo during the lead-in, fading out exactly as the first
    # object's approach begins (matches normal renders — no standalone intro).
    # SHORT pre-roll: begin a fixed lead-in BEFORE the first object SPAWNS, so
    # the render goes straight into gameplay and any long musical intro before
    # the first note is SKIPPED (music slices in from here too — see lay_music).
    # The old `min(0.0, …)` pinned the start to map-time 0, so a map with a 10 s
    # intro rendered 10 s of dimmed background + a static logo = the "long ass
    # intro". The lead-in is sized so the logo still reads (>LOGO_MIN_WINDOW_MS).
    speed = beatmap.diff.speed or 1.0
    _first_obj = min((o.get_start_time() for o in beatmap.hit_objects), default=1000.0)
    _first_spawn = _first_obj - beatmap.diff.preempt
    MERGE_LEAD_IN_MS = 1200.0
    render_start_ms = _first_spawn - MERGE_LEAD_IN_MS
    _logo_start = render_start_ms
    if start_seconds is not None:          # mid-section render (e.g. break test): no branding
        render_start_ms = start_seconds * 1000.0
        _logo_start = None

    scene = StdScene(
        beatmap, [], cam, spr, bodies, bank,
        judgments=None, draw_cursor=False,
        merge_cursors=merge_cursors, merge_board=board,
        merge_misses=merge_misses,
        skin_elems=skin_elems, mods=field_mods,
        draw_judgment_popups=False, draw_hit_lighting=False,
        combo_colors_from_beatmap=True,
        bg_key=bg_key, bg_draw_size=bg_size, dim_envelope=dim_env,
        logo_start_ms=_logo_start,       # showdown!mrgd branding, fades into gameplay
        track_override=(0.13, 0.14, 0.17),  # danser-style DARK slider body so the
                                            # tinted cursors read; heads stay combo-coloured
    )

    end_ms = beatmap.length + 2000.0
    if max_seconds:
        # max_seconds == N seconds of OUTPUT from where we start rendering.
        # (render_start_ms is no longer ~0 for a late first note, so count from
        # it — a 0-base could go negative and produce a zero-length render.)
        end_ms = min(end_ms, render_start_ms + max_seconds * 1000.0)
    total_wall_ms = (end_ms - render_start_ms) / max(speed, 1e-6)
    # Breakdown: slow-mo the map's hardest aim-strain sections (Phase 1 —
    # video only; audio dropped since the pre-built WAV can't match a
    # variable-rate timeline).
    _breakdown_rate = None
    if breakdown:
        _breakdown_rate, _bd_windows = _build_breakdown_rate(
            osu_path, speed, _first_obj, end_ms)
        if _breakdown_rate is not None:
            _extra = sum((we - ws) / _BREAKDOWN_SLOW - (we - ws) / max(speed, 1e-6)
                         for ws, we in _bd_windows)
            total_wall_ms += _extra
            print(f"  breakdown: {len(_bd_windows)} slow-mo window(s), "
                  f"+{_extra / 1000:.1f}s", file=sys.stderr)

    # audio: loudnorm'd MUSIC + a single AUTOPLAY hitsound track (the cleanest
    # play's judged hits — NOT every player's, which would be a cacophony).
    # loudnorm the music ALONE, hitsounds on top, clamp-only limiter on encode
    # (rides the duck-fix → no pumping).
    audio_path = None
    from .record.audio import AudioError, AudioMixer, decode_to_pcm
    mixer = AudioMixer(total_wall_ms)
    have_audio = False
    afile = beatmap.get_audio_file(Path(beatmap_dir))
    if afile is not None:
        try:
            rate_pitch = bool(getattr(players[0][1], "rate_pitch", False))
            pcm = decode_to_pcm(afile, rate=speed, pitch=rate_pitch, loudnorm=True)
            # music begins at map-time 0 → wall offset past the branding pre-roll
            mixer.lay_music(pcm, -render_start_ms / max(speed, 1e-6), volume=1.0)
            have_audio = True
            print(f"  audio: music from {afile.name}", file=sys.stderr)
        except AudioError as e:
            print(f"  audio: music failed: {e}", file=sys.stderr)
    if ref_sim is not None:
        try:
            from .record.hitsounds import (SampleBank, synth_style_for,
                                            collect_hitsound_events, mix_hitsounds)
            sbank = SampleBank(skin=skin, beatmap_dir=Path(beatmap_dir),
                               use_beatmap_samples=True,
                               synth_style=synth_style_for(skin_dir is not None, False))
            layered = getattr(getattr(skin, "info", None), "layered_hit_sounds", True)
            oneshots, loops = collect_hitsound_events(beatmap, ref_sim, layered=layered)
            st = mix_hitsounds(mixer, sbank, oneshots, loops, speed=speed,
                               start_ms=render_start_ms, gain=1.0)
            have_audio = have_audio or st.oneshots > 0
            print(f"  hitsounds: {st.oneshots} one-shots ({ref_name})",
                  file=sys.stderr)
        except Exception as e:
            print(f"  hitsounds skipped: {e}", file=sys.stderr)
    else:
        print("  hitsounds: none", file=sys.stderr)
    if have_audio and not breakdown:
        audio_path = Path(output).with_suffix(".audio.wav")
        mixer.write_wav(audio_path)

    encoder = probe_encoder("auto")
    cmd = build_ffmpeg_cmd(encoder=encoder, resolution=(w, h), fps=fps,
                           output_path=Path(output), audio_path=audio_path, loudnorm=False)
    player = ScenePlayer(scene, end_ms, speed=speed, start_ms=render_start_ms,
                         rate_fn=_breakdown_rate)
    with FfmpegPipe(cmd) as pipe:
        n = RecordPipeline(fps, pipe.push).run(player, total_ms=total_wall_ms)
    if audio_path is not None:
        try:
            audio_path.unlink()
        except OSError:
            pass
    print(f"merge done: {len(players)} players, {n} frames, board={show_board} -> {output}",
          file=sys.stderr)
    return 0


def main(argv=None):
    """CLI entry so the worker can invoke the merge renderer as a SUBPROCESS in
    the osu-std venv (same pattern as StdRenderer → osu_std_renderer CLI)."""
    import argparse
    ap = argparse.ArgumentParser(
        prog="osu_std_renderer.merge",
        description="showdown!mrgd — N osu!std replays on ONE shared field")
    ap.add_argument("--replays", required=True,
                    help="comma-separated .osr paths (2-100); all same map + mods")
    ap.add_argument("--beatmap-dir", required=True, help="dir with the .osu + assets")
    ap.add_argument("--output", required=True, help="output .mp4 path")
    ap.add_argument("--skin", default=None, help="master field skin dir")
    ap.add_argument("--default-skin", default=None, help="fallback/default skin dir")
    ap.add_argument("--resolution", default="1920x1080")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--max-seconds", type=float, default=None)
    ap.add_argument("--start", type=float, default=None,
                    help="start map-time in seconds (mid-section render; skips branding)")
    ap.add_argument("--no-board", action="store_true",
                    help="hide the ScoreV3 leaderboard (on by default)")
    ap.add_argument("--breakdown", action="store_true",
                    help="slow-mo the hardest aim-strain sections (silent)")
    ap.add_argument("--hitsounds", choices=("perfect", "score", "acc", "none"),
                    default="perfect",
                    help="autoplay hitsound source: perfect robotic autoplay "
                         "(default), the top-SCORE play's hits, the top-ACCURACY "
                         "play's hits, or none")
    ap.add_argument("--player-colors", default=None,
                    help="comma-separated per-player cursor hex aligned to "
                         "--replays (empty slot = auto). R3D is always red.")
    args = ap.parse_args(argv)
    w, h = (int(x) for x in args.resolution.lower().split("x"))
    paths = [p.strip() for p in args.replays.split(",") if p.strip()]
    pcolors = (args.player_colors.split(",")
               if args.player_colors is not None else None)
    return render_merge(
        paths, args.beatmap_dir, args.output,
        skin_dir=args.skin, default_skin_dir=args.default_skin,
        resolution=(w, h), fps=args.fps, max_seconds=args.max_seconds,
        show_board=not args.no_board, hitsounds=args.hitsounds,
        start_seconds=args.start, breakdown=args.breakdown,
        player_colors=pcolors)


if __name__ == "__main__":
    import os as _os
    _rc = main() or 0
    sys.stdout.flush()
    sys.stderr.flush()
    # Skip interpreter teardown: at full map length the moderngl/EGL context
    # destructor hangs ~5 min after the mp4 is already written+closed, which
    # would hold the GPU job slot. The output is complete by here, so exit hard.
    _os._exit(int(_rc))
