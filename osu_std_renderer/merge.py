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
        self.fmono = _font(True, int(self.rh * 0.72))
        self.fname = _font(False, int(self.rh * 0.74))
        self.fhdr = _font(False, int(self.hh * 0.60))
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
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        rows = []
        for name, col, hud, end_ms in self.entries:
            sc = hud.score_at(t); ac = hud.acc_at(t) * 100.0
            _, c100, c50, cmiss = hud.counts_at(t)
            dead = end_ms is not None and t > end_ms + 500.0
            rows.append((sc, ac, (c100, c50, cmiss), hud.grade_at(t), name, col, dead))
        rows.sort(key=lambda r: (-r[0], -self._final.get(r[4], 0)))  # tie → final result
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        a = 1.0 if dt <= 0 else min(1.0, 1.0 - 2.718281828 ** (-dt / 55.0))
        # styled "showdown!mrgd" brand label at the top of the board (red !mrgd
        # accent matching the R logo). The only persistent chrome besides it.
        d.text((7, 5), "showdown", font=self.fhdr, fill=(0, 0, 0, 190))
        d.text((6, 4), "showdown", font=self.fhdr, fill=(238, 238, 240, 255))
        _wsd = self.fhdr.getlength("showdown")
        d.text((7 + _wsd, 5), "!mrgd", font=self.fhdr, fill=(0, 0, 0, 190))
        d.text((6 + _wsd, 4), "!mrgd", font=self.fhdr, fill=(233, 58, 60, 255))
        y0 = self.hh + 4
        for i, (sc, ac, (c100, c50, c0), g, name, col, dead) in enumerate(rows[:self.N]):
            target = y0 + i * self.rh
            cur = self._ypos.get(name, target)
            cur += (target - cur) * a
            self._ypos[name] = cur
            y = int(round(cur))
            ra = 0.42 if dead else 1.0
            gcol = _GRADE_COL.get(g, (1.0, 1.0, 1.0))
            isc = int(sc)
            scr = f"{isc % 1_000_000:06d}" if isc < 1_000_000 else f"{isc:07d}"
            acc = f"{ac:5.2f}" if ac < 100.0 else "100.0"

            def _seg(x, text, rgbf):
                cc = tuple(int(max(0.0, min(1.0, v)) * 255) for v in rgbf)
                d.text((x + 1, y + 1), text, font=self.fmono, fill=(0, 0, 0, int(170 * ra)))
                d.text((x, y), text, font=self.fmono, fill=(*cc, int(255 * ra)))
                return x + self.fmono.getlength(text)

            x = 6.0
            x = _seg(x, scr, gcol)             # score  — grade colour
            x = _seg(x, "  ", gcol)
            x = _seg(x, acc, gcol)             # acc    — grade colour
            x = _seg(x, "  ", gcol)
            x = _seg(x, str(c100), _COL_100)   # 100    — green
            x = _seg(x, " ", gcol)
            x = _seg(x, str(c50), _COL_50)     # 50     — yellow
            x = _seg(x, " ", gcol)
            x = _seg(x, str(c0), _COL_MISS)    # miss   — red
            nx = int(x + 8)
            gi = self._grades.get(g)           # grade  — skin ranking image
            if gi is not None:
                if ra < 1.0:                   # dim the badge for a dead row too
                    r_, g_, b_, a_ = gi.split()
                    gi = Image.merge("RGBA", (r_, g_, b_,
                                              a_.point(lambda v: int(v * ra))))
                img.alpha_composite(gi, (nx, int(y + (self.rh - gi.height) / 2)))
                nx += gi.width + 8
            else:
                nx = int(_seg(float(nx), g, gcol)) + 8
            nm = name[:14]                     # name   — cursor colour
            rgb = tuple(int(max(0.0, min(1.0, v)) * 255) for v in col)
            d.text((nx + 1, y + 1), nm, font=self.fname, fill=(0, 0, 0, int(170 * ra)))
            d.text((nx, y), nm, font=self.fname, fill=(*rgb, int(255 * ra)))
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


def render_merge(osr_paths, beatmap_dir, output, *,
                 skin_dir=None, default_skin_dir=None,
                 resolution=(1920, 1080), fps=60, max_seconds=None,
                 show_board=False, truncate=None, hitsounds="perfect",
                 start_seconds=None):
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
    mmods = players[0][1].mods

    osu_path = find_osu_file(Path(beatmap_dir), players[0][1].beatmap_md5)
    beatmap = load_full(osu_path, mods=mmods)
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
        _whiten_skin_cursor(skin_dir)
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

    cols = _colors(len(players))
    merge_cursors = [(frames, cols[i], frames[-1].time_ms if frames else 0.0)
                     for i, (frames, _m) in enumerate(players)]

    # score every player (lazer ScoreV3) once — powers the board AND the
    # score/acc hitsound-reference modes.
    from .ruleset.ruleset import StdRuleset, JudgmentKind
    from .render.hud import HudData
    sims = [StdRuleset(beatmap, fr, mt).run() for fr, mt in players]
    huds = [HudData(s) for s in sims]

    # per-player MISS markers (a colored X at each miss, in the player's colour)
    merge_misses = []
    for i, sim in enumerate(sims):
        ms = []
        for o in beatmap.hit_objects:
            v = sim.verdict_for(o)
            if v is not None and v.kind is JudgmentKind.MISS:
                ms.append((v.deadline or o.get_start_time(), v.popup_pos))
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
    # Add a minimal pre-roll ONLY when the map starts too fast for the logo to
    # read; maps with a natural lead-in get no added dead time.
    speed = beatmap.diff.speed or 1.0
    _first_obj = min((o.get_start_time() for o in beatmap.hit_objects), default=1000.0)
    _first_spawn = _first_obj - beatmap.diff.preempt
    render_start_ms = min(0.0, _first_spawn - 1000.0)
    _logo_start = render_start_ms
    if start_seconds is not None:          # mid-section render (e.g. break test): no branding
        render_start_ms = start_seconds * 1000.0
        _logo_start = None

    scene = StdScene(
        beatmap, [], cam, spr, bodies, bank,
        judgments=None, draw_cursor=False,
        merge_cursors=merge_cursors, merge_board=board,
        merge_misses=merge_misses,
        skin_elems=skin_elems, mods=mmods,
        draw_judgment_popups=False, draw_hit_lighting=False,
        combo_colors_from_beatmap=True,
        bg_key=bg_key, bg_draw_size=bg_size, dim_envelope=dim_env,
        logo_start_ms=_logo_start,       # showdown!mrgd branding, fades into gameplay
        track_override=(0.13, 0.14, 0.17),  # danser-style DARK slider body so the
                                            # tinted cursors read; heads stay combo-coloured
    )

    end_ms = beatmap.length + 2000.0
    if max_seconds:
        _base = render_start_ms if start_seconds is not None else 0.0
        end_ms = min(end_ms, _base + max_seconds * 1000.0)
    total_wall_ms = (end_ms - render_start_ms) / max(speed, 1e-6)

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
    if have_audio:
        audio_path = Path(output).with_suffix(".audio.wav")
        mixer.write_wav(audio_path)

    encoder = probe_encoder("auto")
    cmd = build_ffmpeg_cmd(encoder=encoder, resolution=(w, h), fps=fps,
                           output_path=Path(output), audio_path=audio_path, loudnorm=False)
    player = ScenePlayer(scene, end_ms, speed=speed, start_ms=render_start_ms)
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
    ap.add_argument("--hitsounds", choices=("perfect", "score", "acc", "none"),
                    default="perfect",
                    help="autoplay hitsound source: perfect robotic autoplay "
                         "(default), the top-SCORE play's hits, the top-ACCURACY "
                         "play's hits, or none")
    args = ap.parse_args(argv)
    w, h = (int(x) for x in args.resolution.lower().split("x"))
    paths = [p.strip() for p in args.replays.split(",") if p.strip()]
    return render_merge(
        paths, args.beatmap_dir, args.output,
        skin_dir=args.skin, default_skin_dir=args.default_skin,
        resolution=(w, h), fps=args.fps, max_seconds=args.max_seconds,
        show_board=not args.no_board, hitsounds=args.hitsounds,
        start_seconds=args.start)


if __name__ == "__main__":
    import os as _os
    _rc = main() or 0
    sys.stdout.flush()
    sys.stderr.flush()
    # Skip interpreter teardown: at full map length the moderngl/EGL context
    # destructor hangs ~5 min after the mp4 is already written+closed, which
    # would hold the GPU job slot. The output is complete by here, so exit hard.
    _os._exit(int(_rc))
