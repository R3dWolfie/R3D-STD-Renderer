"""CLI entrypoint — mirrors the adapter contract of the catch renderer
(mania_ordr/catch_renderer.py shells out to `python -m osu_catch_renderer
REPLAY BEATMAP_DIR -o out.mp4 …`; the future std_renderer.py adapter will
call this module identically):

    python -m osu_std_renderer REPLAY.osr BEATMAP_DIR -o out.mp4 \
        [--resolution 1920x1080] [--fps 60] [--encoder auto] [--skin DIR] …

SCAFFOLD STATE: parsing (beatmap + replay + skin.ini) is live; the draw /
record phases are stubs, so a plain render invocation exits with a clear
error. `--parse-only` runs the full parse pipeline and prints the summary
(the adapter's progress regex is not consumed by it). Progress lines match
catch's `rendering… NN%` shape when rendering lands.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .beatmap import load_full
from .beatmap.objects import Slider, Spinner
from .replay import parse_replay
from .settings import StdRenderSettings
from .skin.skin_ini import load as load_skin_ini


def _resolution(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


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
    return ap


def find_osu_file(beatmap: Path, replay_md5: str) -> Path:
    """A direct .osu path, or the (md5-cached) beatmap dir's single .osu."""
    if beatmap.is_file():
        return beatmap
    candidates = sorted(beatmap.glob("*.osu"))
    if not candidates:
        raise FileNotFoundError(f"no .osu in {beatmap}")
    return candidates[0]


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

    print("error: the std draw/record phases are not implemented yet — this "
          "is the scaffold build. Run with --parse-only, or see "
          "render/slider_body.py (Phase-0 spike) for what lands next.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
