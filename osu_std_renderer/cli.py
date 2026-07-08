"""CLI entrypoint — mirrors the adapter contract of the catch renderer
(mania_ordr/catch_renderer.py shells out to `python -m osu_catch_renderer
REPLAY BEATMAP_DIR -o out.mp4 …`; the future std_renderer.py adapter will
call this module identically):

    python -m osu_std_renderer REPLAY.osr BEATMAP_DIR -o out.mp4 \
        [--resolution 1920x1080] [--fps 60] [--encoder auto] [--skin DIR] …

PHASE-1 STATE (the "first moving render" build): the full record path is
live — procedural textures (render/textures.py), object lifecycle + scene
(render/scene.py), slider bodies (render/slider_body.py), cursor+trail
from the replay, fixed-timestep record loop (record/pipeline.py) into the
single-process ffmpeg pipe (record/encode.py) with offline-mixed music
(record/audio.py — music only; hitsounds are a later phase). Judgments are
NOT simulated yet: objects are assumed hit at startTime, so there is no
HUD/score/misses. Spinners render nothing (logged). Progress lines match
catch's `rendering… NN%` shape.

Debug extras:
    --parse-only            parse map+replay+skin, print a summary, exit 0
    --max-seconds N         stop the render N seconds in (MVP clips)
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
    ap.add_argument("--key-overlay", action=BA, default=True)
    ap.add_argument("--pp-counter", action=BA, default=True)
    ap.add_argument("--hit-counter", action=BA, default=False)
    ap.add_argument("--hit-error-meter", action=BA, default=True)
    ap.add_argument("--show-combo", action=BA, default=True)
    ap.add_argument("--show-score", action=BA, default=True)
    ap.add_argument("--show-hp", action=BA, default=True)
    ap.add_argument("--show-grade", action=BA, default=True)
    ap.add_argument("--show-mods", action=BA, default=True)
    ap.add_argument("--watermark", default="")
    ap.add_argument("--music-volume", type=int, default=100)
    ap.add_argument("--hitsound-volume", type=int, default=100)
    ap.add_argument("--general-volume", type=int, default=100)
    ap.add_argument("--audio-offset", type=int, default=0, help="ms; -earlier")
    ap.add_argument("--bg-dim-intro", type=int, default=0)
    ap.add_argument("--bg-dim-game", type=int, default=80)
    ap.add_argument("--bg-dim-breaks", type=int, default=30)
    ap.add_argument("--bg-blur", type=int, default=0)
    ap.add_argument("--results-seconds", type=float, default=None)
    ap.add_argument("--parse-only", action="store_true",
                    help="parse map+replay+skin, print a summary, exit 0")
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
            beatmap_dir: Path, skin_info) -> int:
    """The Phase-1 record path: scene → record loop → ffmpeg."""
    # GL-touching imports live here so --parse-only works GL-less
    from .record.audio import AudioError, AudioMixer, decode_to_pcm
    from .record.encode import FfmpegPipe, build_ffmpeg_cmd, probe_encoder
    from .record.pipeline import RecordPipeline
    from .render.gl import SpriteRenderer
    from .render.playfield import PlayfieldCamera
    from .render.scene import ScenePlayer, StdScene, log_skips
    from .render.slider_body import SliderBodyRenderer
    from .render.textures import TextureBank

    w, h = settings.resolution
    spr = SpriteRenderer(w, h)
    bank = TextureBank(spr)
    bodies = SliderBodyRenderer(spr.ctx, w, h)
    cam = PlayfieldCamera(w, h)

    combo_colors = [(r / 255.0, g / 255.0, b / 255.0)
                    for r, g, b in skin_info.combo_colors]
    scene = StdScene(
        beatmap, frames, cam, spr, bodies, bank,
        combo_colors=combo_colors,
        snaking_in=settings.slider_snaking_in,
        draw_approach_circles=settings.draw_approach_circles,
        draw_combo_numbers=settings.draw_combo_numbers,
        draw_cursor=settings.draw_cursor,
        cursor_scale=settings.cursor_scale,
    )

    last_end = max(o.get_end_time() for o in beatmap.hit_objects)
    end_ms = last_end + HIT_FADE_OUT + settings.fade_out_time * 1000.0
    if args.max_seconds is not None:
        end_ms = min(end_ms, args.max_seconds * 1000.0)
    speed = beatmap.diff.speed

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
            mixer = AudioMixer(end_ms / speed)
            vol = ((settings.music_volume / 100.0)
                   * (settings.general_volume / 100.0))
            mixer.lay_music(pcm, 0.0, volume=vol)
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

    total_wall_ms = end_ms / speed
    last_pct = [-1]

    def progress(frac: float) -> None:
        pct = int(frac * 100)
        if pct != last_pct[0]:
            last_pct[0] = pct
            print(f"rendering… {pct}%", file=sys.stderr, flush=True)

    player = ScenePlayer(scene, end_ms, speed=speed)
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
    print(f"done: {n_frames} frames in {wall:.1f}s "
          f"({n_frames / wall:.1f} fps render, encoder {encoder}) → {output}",
          file=sys.stderr)
    spr.release()
    return 0


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
        cursor_scale=args.cursor_scale, show_key_overlay=args.key_overlay,
        show_pp_counter=args.pp_counter, show_hit_counter=args.hit_counter,
        show_hit_error_meter=args.hit_error_meter, show_combo=args.show_combo,
        show_score=args.show_score, show_hp_bar=args.show_hp,
        show_grade=args.show_grade, show_mods=args.show_mods,
        watermark_text=args.watermark, music_volume=args.music_volume,
        hitsound_volume=args.hitsound_volume,
        general_volume=args.general_volume, audio_offset=args.audio_offset,
        bg_dim_intro=args.bg_dim_intro, bg_dim_game=args.bg_dim_game,
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
    return _render(args, settings, beatmap, frames, beatmap_dir, skin_info)


if __name__ == "__main__":
    raise SystemExit(main())
