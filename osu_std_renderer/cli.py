"""CLI entrypoint — mirrors the adapter contract of the catch renderer
(mania_ordr/catch_renderer.py shells out to `python -m osu_catch_renderer
REPLAY BEATMAP_DIR -o out.mp4 …`; the future std_renderer.py adapter will
call this module identically):

    python -m osu_std_renderer REPLAY.osr BEATMAP_DIR -o out.mp4 \
        [--resolution 1920x1080] [--fps 60] [--encoder auto] [--skin DIR] …

CURRENT STATE (Phase-1 record path + the judgment phase): procedural
textures (render/textures.py), object lifecycle + scene (render/scene.py),
slider bodies (render/slider_body.py), cursor+trail from the replay,
fixed-timestep record loop (record/pipeline.py) into the single-process
ffmpeg pipe (record/encode.py) with offline-mixed music (record/audio.py —
music only; hitsounds are a later phase). Judgments ARE simulated
(ruleset/ruleset.py — ported ppy/osu logic, stable notelock + classic
sliders, reconciled to the .osr's authoritative counts; the pre-reconcile
sim-vs-real delta is printed as the honesty metric). Explosions fire at
real hit times, misses fade out, judgment popups show, sliderbreaks dim
the ball. The §4.6 gameplay HUD is live (render/hud.py — score/acc/grade/
progress/combo/hit-error+UR/key-overlay/break-flash in the §5.3 virtual
1080p UI space; a `hud:` final-values line prints after each render).
Spinners render nothing (logged; simplified judgment). Progress lines
match catch's `rendering… NN%` shape.

BACKGROUND + SKIN PHASE: the map's `[Events]` background renders under
everything with the §4.10 dim envelope (render/background.py; intro/game/
break dims from the R3D preset keys, missing bg fails soft to the dark
void). `--skin DIR` loads a real skin's core gameplay textures
(render/skin_elements.py) — per-element fallback to the procedural set;
a `skin:` report line lists what came from the skin.

ARROWS + TICKS + FOLLOW POINTS PHASE (render/markers.py + scene draws):
reverse arrows (beat-pulsed, white, tangent-oriented), slider ticks of
the active span, follow points on the lazer schedule (--follow-points
honors DrawFollowPoints), sliderstartcircle/sliderendcircle head/tail
specialisations, sliderb/followpoint AnimationFramerate cycling, and the
§3.3 two-mode skin cursor trail (cursormiddle → long connected trail,
else sparse 16.67 ms drops; --force-long-trail = §4.7 ForceLongTrail).

Debug extras:
    --parse-only            parse map+replay+skin, print a summary, exit 0
    --start N               start the render N seconds into the map
    --max-seconds N         stop the render N seconds in (MVP clips;
                            --start 35 --max-seconds 45 → a 35s-45s clip)
    --dump-frames "a,b,c"   write single-frame PNGs at map times a,b,c (ms)
                            instead of encoding video
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

from .beatmap import load_full
from .beatmap.difficulty import HIT_FADE_OUT
from .beatmap.objects import Slider, Spinner
from .replay import parse_replay
from .settings import StdRenderSettings
from .skin.skin_ini import load as load_skin_ini


def _resolution(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def _ms_list(s: str) -> list[float]:
    return [float(tok) for tok in s.split(",") if tok.strip()]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="osu_std_renderer")
    ap.add_argument("osr", type=Path, help="replay .osr file")
    ap.add_argument("beatmap", type=Path,
                    help="dir with .osu + audio + bg, or a direct .osu path")
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--resolution", type=_resolution, default=(1920, 1080))
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--encoder", default="auto",
                    help="auto | h264_nvenc | h264_vaapi | libx264")
    ap.add_argument("--encoder-device", default=None)
    ap.add_argument("--skin", type=Path, default=None,
                    help="extracted skin dir (resolver-provided .osk)")
    ap.add_argument("--default-skin", type=Path, default=None)
    BA = argparse.BooleanOptionalAction
    ap.add_argument("--skip-intro", action=BA, default=True)
    ap.add_argument("--results", action=BA, default=True)
    ap.add_argument("--letterbox-breaks", action=BA, default=True)
    ap.add_argument("--approach-circles", action=BA, default=True)
    ap.add_argument("--combo-numbers", action=BA, default=True)
    ap.add_argument("--follow-points", action=BA, default=True)
    ap.add_argument("--snaking-in", action=BA, default=True)
    ap.add_argument("--snaking-out", action=BA, default=True)
    ap.add_argument("--cursor", action=BA, default=True)
    ap.add_argument("--skin-cursor", action=BA, default=False)
    ap.add_argument("--cursor-scale", type=float, default=1.0)
    ap.add_argument("--force-long-trail", action=BA, default=False,
                    help="§4.7 ForceLongTrail: long connected skin trail "
                         "even without cursormiddle")
    ap.add_argument("--key-overlay", action=BA, default=True)
    ap.add_argument("--pp-counter", action=BA, default=True)
    ap.add_argument("--hit-counter", action=BA, default=False)
    ap.add_argument("--hit-error-meter", action=BA, default=True)
    ap.add_argument("--unstable-rate", action=BA, default=True)
    ap.add_argument("--show-combo", action=BA, default=True)
    ap.add_argument("--show-score", action=BA, default=True)
    ap.add_argument("--show-hp", action=BA, default=True)
    ap.add_argument("--show-grade", action=BA, default=True)
    ap.add_argument("--show-mods", action=BA, default=True)
    ap.add_argument("--show-progress", action=BA, default=True)
    ap.add_argument("--progress-style", choices=("pie", "bar"), default="pie")
    ap.add_argument("--hud-scale", type=float, default=1.0)
    ap.add_argument("--hud-opacity", type=float, default=1.0)
    ap.add_argument("--break-flash", action=BA, default=True,
                    help="red edge-vignette pulse on combo breaks")
    ap.add_argument("--watermark", default="")
    ap.add_argument("--music-volume", type=int, default=100)
    ap.add_argument("--hitsound-volume", type=int, default=100)
    ap.add_argument("--general-volume", type=int, default=100)
    ap.add_argument("--audio-offset", type=int, default=0, help="ms; -earlier")
    ap.add_argument("--bg-dim-intro", type=int, default=0)
    ap.add_argument("--bg-dim-game", type=int, default=90)
    ap.add_argument("--bg-dim-breaks", type=int, default=30)
    ap.add_argument("--bg-dim", type=int, default=None,
                    help="override --bg-dim-game (gameplay dim, 0-100)")
    ap.add_argument("--bg-blur", type=int, default=0)
    ap.add_argument("--results-seconds", type=float, default=None)
    ap.add_argument("--parse-only", action="store_true",
                    help="parse map+replay+skin, print a summary, exit 0")
    ap.add_argument("--start", type=float, default=None,
                    help="start the render this many seconds into the map")
    ap.add_argument("--max-seconds", type=float, default=None,
                    help="stop the render this many seconds in (debug/MVP)")
    ap.add_argument("--dump-frames", type=_ms_list, default=None,
                    metavar="MS,MS,…",
                    help="write single-frame PNGs at these map times "
                         "instead of encoding video")
    return ap


def find_osu_file(beatmap: Path, replay_md5: str) -> Path:
    """A direct .osu path, or the md5-matching .osu inside the beatmap dir
    (cached sets hold every diff — the replay's beatmap hash picks the
    right one; first-sorted is only the no-hash fallback)."""
    if beatmap.is_file():
        return beatmap
    candidates = sorted(beatmap.glob("*.osu"))
    if not candidates:
        raise FileNotFoundError(f"no .osu in {beatmap}")
    if replay_md5:
        for cand in candidates:
            if hashlib.md5(cand.read_bytes()).hexdigest() == replay_md5.lower():
                return cand
    return candidates[0]


def _render(args, settings: StdRenderSettings, beatmap, frames,
            beatmap_dir: Path, skin_info, judgments=None) -> int:
    """The Phase-1 record path: scene → record loop → ffmpeg."""
    # GL-touching imports live here so --parse-only works GL-less
    from .record.audio import AudioError, AudioMixer, decode_to_pcm
    from .record.encode import FfmpegPipe, build_ffmpeg_cmd, probe_encoder
    from .record.pipeline import RecordPipeline
    from .render.background import (build_dim_envelope, cover_size,
                                    load_background)
    from .render.gl import SpriteRenderer
    from .render.hud import StdHud
    from .render.playfield import PlayfieldCamera
    from .render.scene import ScenePlayer, StdScene, log_skips
    from .render.skin_elements import SkinElements
    from .render.slider_body import SliderBodyRenderer
    from .render.textures import TextureBank
    from .skin.skin import Skin

    w, h = settings.resolution
    spr = SpriteRenderer(w, h)
    bank = TextureBank(spr)
    bodies = SliderBodyRenderer(spr.ctx, w, h)
    cam = PlayfieldCamera(w, h)

    # --- real-skin core textures (per-element procedural fallback) ---------------
    skin_elems = None
    if settings.skin_dir is not None:
        skin = Skin(skin_dir=settings.skin_dir,
                    fallback_dir=settings.default_skin_dir)
        skin_elems = SkinElements(skin, spr)
        for line in skin_elems.report_lines():
            print(line, file=sys.stderr)

    # --- §4.10 background + dim envelope (fail-soft to the dark void) -------------
    bg_key = bg_size = dim_env = None
    bg_path = beatmap.get_related_file(beatmap_dir, beatmap.bg)
    if bg_path is not None:
        rgba = load_background(bg_path)
        if rgba is not None:
            spr.upload_texture("background", rgba)
            bg_key = "background"
            bg_size = cover_size(w, h, rgba.shape[1], rgba.shape[0])
            dim_env = build_dim_envelope(
                settings.bg_dim_intro / 100.0, settings.bg_dim_game / 100.0,
                settings.bg_dim_breaks / 100.0,
                [o.get_start_time() for o in beatmap.hit_objects],
                beatmap.diff.preempt, beatmap.pauses)
        else:
            print(f"WARNING: background '{beatmap.bg}' failed to decode — "
                  "rendering without background", file=sys.stderr)
    elif beatmap.bg:
        print(f"WARNING: background '{beatmap.bg}' not found in the beatmap "
              "dir — rendering without background", file=sys.stderr)

    # the §4.6 HUD needs the judgment stream; --dump-frames without a
    # replay sim just renders HUD-less (the Phase-1 fallback)
    hud = None
    if judgments is not None:
        hud = StdHud(spr, bank, settings, judgments, frames, beatmap)

    combo_colors = [(r / 255.0, g / 255.0, b / 255.0)
                    for r, g, b in skin_info.combo_colors]
    track_override = None
    if skin_info.slider_track_override is not None:
        track_override = tuple(c / 255.0
                               for c in skin_info.slider_track_override)
    scene = StdScene(
        beatmap, frames, cam, spr, bodies, bank,
        combo_colors=combo_colors,
        snaking_in=settings.slider_snaking_in,
        draw_approach_circles=settings.draw_approach_circles,
        draw_combo_numbers=settings.draw_combo_numbers,
        draw_follow_points=settings.draw_follow_points,
        draw_cursor=settings.draw_cursor,
        cursor_scale=settings.cursor_scale,
        force_long_trail=settings.cursor_long_trail,
        judgments=judgments,
        hud=hud,
        skin_elems=skin_elems,
        use_skin_cursor=settings.use_skin_cursor,
        border_color=tuple(c / 255.0 for c in skin_info.slider_border),
        track_override=track_override,
        bg_key=bg_key,
        bg_draw_size=bg_size,
        dim_envelope=dim_env,
    )

    last_end = max(o.get_end_time() for o in beatmap.hit_objects)
    end_ms = last_end + HIT_FADE_OUT + settings.fade_out_time * 1000.0
    if args.max_seconds is not None:
        end_ms = min(end_ms, args.max_seconds * 1000.0)
    start_ms = (args.start or 0.0) * 1000.0
    speed = beatmap.diff.speed
    if start_ms and start_ms >= end_ms:
        print(f"error: --start {args.start:g}s is at/after the render end "
              f"({end_ms / 1000.0:.1f}s)", file=sys.stderr)
        return 2

    # --- keyframe dump mode -----------------------------------------------------
    if args.dump_frames:
        from PIL import Image
        out_dir = args.output.parent if args.output else Path.cwd()
        stem = args.output.stem if args.output else "frame"
        out_dir.mkdir(parents=True, exist_ok=True)
        for t in sorted(args.dump_frames):
            rgb = scene.frame_rgb(t)
            p = out_dir / f"{stem}_t{int(round(t))}ms.png"
            Image.fromarray(rgb).save(p)
            print(f"wrote {p}", file=sys.stderr)
        log_skips(scene)
        _print_hud_final_values(hud, judgments)
        spr.release()
        return 0

    if args.output is None:
        print("error: -o/--output is required to render", file=sys.stderr)
        return 2
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    # --- offline audio (music only this phase; NO-BASS design) -------------------
    audio_path = None
    afile = beatmap.get_audio_file(beatmap_dir)
    if afile is not None:
        try:
            pcm = decode_to_pcm(afile, rate=speed)
            mixer = AudioMixer((end_ms - start_ms) / speed)
            vol = ((settings.music_volume / 100.0)
                   * (settings.general_volume / 100.0))
            # --start window: map-time start_ms lands at wall t=0, so the
            # (already rate-adjusted) music is laid start_ms/speed early —
            # mix_at clips the negative head
            mixer.lay_music(pcm, -start_ms / speed, volume=vol)
            audio_path = output.with_suffix(".audio.wav")
            mixer.write_wav(audio_path)
        except AudioError as e:
            print(f"WARNING: audio mix failed, rendering SILENT video: {e}",
                  file=sys.stderr)
            audio_path = None
    else:
        print(f"WARNING: beatmap audio '{beatmap.audio}' not found — "
              "rendering SILENT video", file=sys.stderr)

    encoder = probe_encoder(settings.encoder)
    cmd = build_ffmpeg_cmd(
        encoder=encoder, resolution=(w, h), fps=settings.fps,
        output_path=output, audio_path=audio_path,
        audio_offset_ms=settings.audio_offset)

    total_wall_ms = (end_ms - start_ms) / speed
    last_pct = [-1]

    def progress(frac: float) -> None:
        pct = int(frac * 100)
        if pct != last_pct[0]:
            last_pct[0] = pct
            print(f"rendering… {pct}%", file=sys.stderr, flush=True)

    player = ScenePlayer(scene, end_ms, speed=speed, start_ms=start_ms)
    t0 = time.monotonic()
    try:
        with FfmpegPipe(cmd) as pipe:
            n_frames = RecordPipeline(settings.fps, pipe.push,
                                      progress=progress).run(
                player, total_ms=total_wall_ms)
    finally:
        if audio_path is not None:
            try:
                audio_path.unlink()
            except OSError:
                pass
    wall = time.monotonic() - t0
    log_skips(scene)
    _print_hud_final_values(hud, judgments)
    print(f"done: {n_frames} frames in {wall:.1f}s "
          f"({n_frames / wall:.1f} fps render, encoder {encoder}) → {output}",
          file=sys.stderr)
    spr.release()
    return 0


def _print_hud_final_values(hud, judgments) -> None:
    """The HUD phase's honesty line: the numbers the LAST frame displays,
    with the sim-vs-replay accuracy check spelled out."""
    if hud is None:
        return
    fv = hud.final_values()
    acc_line = f"acc {fv['acc'] * 100.0:.2f}%"
    if judgments is not None and judgments.real_counts is not None:
        c3, c1, c5, cm = judgments.real_counts
        total = c3 + c1 + c5 + cm
        real_acc = ((300 * c3 + 100 * c1 + 50 * c5) / (300.0 * total)
                    if total else 1.0)
        ok = "==" if abs(real_acc - fv["acc"]) < 5e-5 else "!= MISMATCH"
        acc_line += f" (replay {real_acc * 100.0:.2f}% {ok})"
    print(f"hud: final score {fv['score']} | {acc_line} | "
          f"combo {fv['combo']}x (max {fv['max_combo']}x) | "
          f"UR {fv['ur']:.1f} | grade {fv['grade']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = StdRenderSettings(
        resolution=args.resolution, fps=args.fps, encoder=args.encoder,
        encoder_device=args.encoder_device, skin_dir=args.skin,
        default_skin_dir=args.default_skin, skip_intro=args.skip_intro,
        show_results=args.results, letterbox_breaks=args.letterbox_breaks,
        draw_approach_circles=args.approach_circles,
        draw_combo_numbers=args.combo_numbers,
        draw_follow_points=args.follow_points,
        slider_snaking_in=args.snaking_in, slider_snaking_out=args.snaking_out,
        draw_cursor=args.cursor, use_skin_cursor=args.skin_cursor,
        cursor_scale=args.cursor_scale,
        cursor_long_trail=args.force_long_trail,
        show_key_overlay=args.key_overlay,
        show_pp_counter=args.pp_counter, show_hit_counter=args.hit_counter,
        show_hit_error_meter=args.hit_error_meter,
        show_unstable_rate=args.unstable_rate, show_combo=args.show_combo,
        show_score=args.show_score, show_hp_bar=args.show_hp,
        show_grade=args.show_grade, show_mods=args.show_mods,
        show_progress=args.show_progress, progress_style=args.progress_style,
        hud_scale=args.hud_scale, hud_opacity=args.hud_opacity,
        combo_break_flash=args.break_flash,
        watermark_text=args.watermark, music_volume=args.music_volume,
        hitsound_volume=args.hitsound_volume,
        general_volume=args.general_volume, audio_offset=args.audio_offset,
        bg_dim_intro=args.bg_dim_intro,
        bg_dim_game=(args.bg_dim if args.bg_dim is not None
                     else args.bg_dim_game),
        bg_dim_breaks=args.bg_dim_breaks, bg_blur=args.bg_blur,
    )
    if args.results_seconds is not None:
        settings.results_screen_time = args.results_seconds

    frames, meta = parse_replay(args.osr)
    osu_path = find_osu_file(args.beatmap, meta.beatmap_md5)
    beatmap = load_full(osu_path, mods=meta.mods)
    skin_info = load_skin_ini(settings.skin_dir)

    print(f"map:    {beatmap.artist} - {beatmap.name} [{beatmap.difficulty_name}] "
          f"by {beatmap.creator} (v{beatmap.version})", file=sys.stderr)
    n_sliders = sum(isinstance(o, Slider) for o in beatmap.hit_objects)
    n_spinners = sum(isinstance(o, Spinner) for o in beatmap.hit_objects)
    n_circles = len(beatmap.hit_objects) - n_sliders - n_spinners
    print(f"objects: {n_circles}c/{n_sliders}s/{n_spinners}sp  "
          f"CS{beatmap.diff.cs:g}→r={beatmap.diff.circle_radius:.2f}px  "
          f"AR{beatmap.diff.ar:g}→preempt={beatmap.diff.preempt}ms",
          file=sys.stderr)
    print(f"replay: {meta.player_name} {meta.count_300}/{meta.count_100}/"
          f"{meta.count_50}/{meta.count_miss} {meta.accuracy}% {meta.grade} "
          f"mods={meta.mods:#x} frames={len(frames)}", file=sys.stderr)
    print(f"skin:   \"{skin_info.name or 'default'}\" v{skin_info.version:g}",
          file=sys.stderr)

    # judgment simulation (pure CPU — runs in --parse-only too, so the
    # sim-vs-real honesty metric is checkable without GL)
    judgments = None
    if frames and meta.mode == 0:
        from .ruleset import StdRuleset
        judgments = StdRuleset(beatmap, frames, meta).run()
        for line in judgments.report_lines():
            print(line, file=sys.stderr)

    if args.parse_only:
        return 0

    if meta.mode != 0:
        print(f"error: replay mode {meta.mode} is not osu!standard",
              file=sys.stderr)
        return 2
    if not frames:
        print("error: replay has no cursor frames", file=sys.stderr)
        return 2

    beatmap_dir = args.beatmap if args.beatmap.is_dir() else args.beatmap.parent
    return _render(args, settings, beatmap, frames, beatmap_dir, skin_info,
                   judgments=judgments)


if __name__ == "__main__":
    raise SystemExit(main())
