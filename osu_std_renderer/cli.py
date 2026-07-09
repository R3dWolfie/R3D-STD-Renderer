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
ffmpeg pipe (record/encode.py) with offline-mixed music (record/audio.py).
Judgments ARE simulated
(ruleset/ruleset.py — ported ppy/osu logic, stable notelock + classic
sliders, reconciled to the .osr's authoritative counts; the pre-reconcile
sim-vs-real delta is printed as the honesty metric). Explosions fire at
real hit times, misses fade out, judgment popups show, sliderbreaks dim
the ball. The §4.6 gameplay HUD is live (render/hud.py — score/acc/grade/
progress/combo/hit-error+UR/key-overlay/break-flash in the §5.3 virtual
1080p UI space; a `hud:` final-values line prints after each render).
Progress lines match catch's `rendering… NN%` shape.

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

SPINNER + HIT LIGHTING PHASE: spinners render (render/spinner.py + scene
_draw_spinner — §3.3 style auto-detect old/new/procedural, replay-driven
rotation, metre/glow progress, approach circle, SPIN!/CLEAR!/RPM,
SpinnerFadePlayfield) and non-miss judgments flash a combo-tinted
`lighting` glow under the popup (--no-hit-lighting; preset default ON).
HITSOUND PHASE (record/hitsounds.py): §3.4 hitsounds mix into the same
offline audio track — the 3×7 sample grid through BEATMAP(custom index)
→ skin chain → synthesized defaults, one-shots at judged hit times
(layered/bit semantics, timing-point volumes floored at 0.08),
slidertick one-shots, sliderslide/sliderwhistle loops tiled over the
ruleset's tracking windows and spinnerspin over spins (--no-hitsounds /
--use-skin-hitsounds / --hitsound-volume control it). Hitsounds are
judgment-driven, so they require a replay; `--no-replay` stays a bare
debug flag (visuals only — no cursor/HUD/judgments/hitsounds).

OWNER PUNCH-LIST PHASE (2026-07): distance-based long cursor trail
(uniform spacing/brightness at any speed), per-element HUD selection
(skin's legacy component where the skin ships it, ARGON otherwise —
hud.py docstring; --legacy-defaults forces the ALL-LEGACY look for HUD
AND the synth hitsound bank, --renderer-default-font-and-ranks wins
over it for numbers/ranks), brighter-inside slider bodies (BodyStyle
defaults), the classic falling hit0 miss (--miss-fall), skin ranking-*
images with --renderer-default-font-and-ranks, multi-reverse arrows
gated on the head hit, the bottom-parked hit-error/UR block,
--playfield-borders, and RED'S results screen as the outro
(render/results.py — --results is now IMPLEMENTED, results_screen_time
honored, mania-card layout).

SETTINGS-SURFACE PHASE (2026-07 — the full R3D website preset surface):
every std setting mania_ordr/presets.py exposes now maps to a flag and
renders (settings.py's table is the authority): nightcore beat overlay,
mod pills, live pp counter (rosu-pp gradual), hit counter, aim-error
scatter, strain graph, warning arrows, bg blur/parallax/triangles/video
(video_bg.py)/flash-to-beat, seizure card + lead-in pre-roll, real
fade-to-black (video+audio), bloom (+to-beat), the R3D logo splash,
cursor trail-scale/rainbow/ripples, slider snaking-out + merge, beatmap
[Colours] vs skin combo colours, and -skip intro trimming.
StdRenderSettings.from_preset(dict) consumes the bot preset JSON
wholesale — the future std_renderer.py service adapter calls that.
Accepted + NO-OP (documented): load_storyboard (deferred subsystem,
danser fallback), show_scoreboard/scoreboard_avatars (render/
scoreboard.py holds the osu!API JSON hand-off stub).

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
import os
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


# visual-only mod overrides (--add-mods): HD/FL are driven purely by the mod
# bitmask and don't alter geometry/timing, so they can be forced on ANY replay
# without re-loading the beatmap or touching judgment reconciliation.
_VISUAL_MOD_BITS = {"HD": 1 << 3, "FL": 1 << 10}


def _mod_list(s: str) -> int:
    bits = 0
    for tok in s.replace(",", " ").split():
        acr = tok.strip().upper()
        if acr not in _VISUAL_MOD_BITS:
            raise argparse.ArgumentTypeError(
                f"--add-mods only forces visual mods {sorted(_VISUAL_MOD_BITS)}"
                f" (got {acr!r})")
        bits |= _VISUAL_MOD_BITS[acr]
    return bits


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="osu_std_renderer")
    ap.add_argument("osr", type=Path, nargs="?", default=None,
                    help="replay .osr file (omit with --no-replay)")
    ap.add_argument("beatmap", type=Path, nargs="?", default=None,
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
    ap.add_argument("--skip-intro", action=BA, default=True,
                    help="trim a long silent intro: start ~1 s before the "
                         "first object's approach (danser -skip semantics; "
                         "only when it saves > 2 s and --start is not given)")
    ap.add_argument("--lead-in", type=float, default=0.0, metavar="SEC",
                    help="§4.10 LeadInTime: extra pre-roll hold before the "
                         "map starts (wall seconds, on top of the intro)")
    ap.add_argument("--fade-out", type=float, default=None, metavar="SEC",
                    help="§4.10 FadeOutTime: video+audio fade to black "
                         "after the last object (wall seconds; default 1.5)")
    ap.add_argument("--results", action=BA, default=True)
    ap.add_argument("--no-fail-animation", dest="no_fail_animation",
                    action="store_true", default=False,
                    help="debug escape hatch: ignore the .osr's death point "
                         "and render a FAILED replay as a pass (no fall/red "
                         "sequence, the replay's own grade instead of F)")
    ap.add_argument("--results-style", choices=("lazer", "r3d"),
                    default="lazer",
                    help="outro: 'lazer' = the ported osu!(lazer) ranking "
                         "screen (score panel → expanded statistics; std "
                         "default), 'r3d' = the in-house shared card")
    ap.add_argument("--letterbox-breaks", action=BA, default=True)
    ap.add_argument("--approach-circles", action=BA, default=True)
    ap.add_argument("--combo-numbers", action=BA, default=True)
    ap.add_argument("--follow-points", action=BA, default=True)
    ap.add_argument("--snaking-in", action=BA, default=True)
    ap.add_argument("--snaking-out", action=BA, default=True)
    ap.add_argument("--slider-merge", action=BA, default=False,
                    help="§4.9 SliderMerge: draw all visible slider bodies "
                         "as ONE union pass (shared borders, no stacking)")
    ap.add_argument("--cursor", action=BA, default=True)
    ap.add_argument("--skin-cursor", action=BA, default=True,
                    help="use the skin's cursor when the skin ships one "
                         "(real osu has no toggle — this is the default; "
                         "--no-skin-cursor forces the procedural cursor)")
    ap.add_argument("--cursor-scale", type=float, default=1.0)
    ap.add_argument("--cursor-trail-scale", type=float, default=1.0,
                    help="§4.8 TrailScale: trail sprite size only")
    ap.add_argument("--cursor-rainbow", action=BA, default=False,
                    help="§4.8 rainbow: hue-cycle the cursor+trail tint")
    ap.add_argument("--cursor-ripples", action=BA, default=False,
                    help="§4.8 ripples: expanding ring per press edge")
    ap.add_argument("--force-long-trail", action=BA, default=False,
                    help="§4.7 ForceLongTrail: long connected skin trail "
                         "even without cursormiddle")
    ap.add_argument("--key-overlay", action=BA, default=True)
    ap.add_argument("--pp-counter", action=BA, default=False,
                    help="§4.6 PPCounter: live rosu-pp gradual pp (site "
                         "default OFF; hides itself if rosu is missing)")
    ap.add_argument("--hit-counter", action=BA, default=False)
    ap.add_argument("--aim-error-meter", action=BA, default=False,
                    help="§4.6 AimErrorMeter: cursor-offset-at-click "
                         "scatter panel (site default OFF)")
    ap.add_argument("--strain-graph", action=BA, default=False,
                    help="§4.6 StrainGraph: bottom strain fill graph "
                         "(rosu strains, else the density proxy)")
    ap.add_argument("--scoreboard", action=BA, default=True,
                    help="§4.6 ScoreBoard: ACCEPTED + NO-OP — needs the "
                         "osu!API leaderboard hand-off (render/"
                         "scoreboard.py documents the JSON contract)")
    ap.add_argument("--scoreboard-avatars", action=BA, default=False,
                    help="ACCEPTED + NO-OP (rides the scoreboard hand-off)")
    ap.add_argument("--warning-arrows", action=BA, default=True,
                    help="§4.6 ShowWarningArrows: flashing arrows before "
                         "gameplay resumes after a break")
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
    ap.add_argument("--hit-lighting", action=BA, default=True,
                    help="§3.3 combo-tinted lighting flash under non-miss "
                         "judgments (R3D preset default ON)")
    ap.add_argument("--miss-fall", action=BA, default=True,
                    help="classic miss animation: the hit0 popup falls + "
                         "slightly rotates while it fades (stable's look; "
                         "owner default ON)")
    ap.add_argument("--renderer-default-font-and-ranks", action=BA,
                    default=False,
                    help="force the renderer's default (Argon) HUD numbers "
                         "+ procedural rank text even when the skin ships "
                         "fonts / ranking-* images (wins over "
                         "--legacy-defaults for numbers/ranks)")
    ap.add_argument("--legacy-defaults", action=BA, default=False,
                    help="the ALL-LEGACY look: every HUD element uses its "
                         "legacy component (skin textures where shipped, "
                         "classic lg_* bakes otherwise — no Argon anywhere, "
                         "even skinless) and missing hitsound samples "
                         "synthesize in the LEGACY sound family; gameplay "
                         "elements already fall back skin-else-legacy")
    ap.add_argument("--playfield-borders",
                    choices=("none", "edges", "full"), default="none",
                    help="subtle white outline of the playfield bounds: "
                         "'full' = thin border box, 'edges' = corner "
                         "markers only")
    ap.add_argument("--watermark", default="")
    ap.add_argument("--music-volume", type=int, default=100)
    ap.add_argument("--hitsound-volume", type=int, default=100)
    ap.add_argument("--hitsounds", action=BA, default=True,
                    help="mix §3.4 hitsounds into the audio track — the "
                         "use_replay_hitsounds preset key (one-shots at "
                         "judged hit times, slide/spin loops)")
    ap.add_argument("--nightcore-hitsounds", action=BA, default=False,
                    help="§4.4 PlayNightcoreSamples: clap each beat + "
                         "finish each bar downbeat (red timing points)")
    ap.add_argument("--use-skin-hitsounds", action=BA, default=False,
                    help="ignore beatmap-folder samples "
                         "(Audio.IgnoreBeatmapSamples)")
    ap.add_argument("--general-volume", type=int, default=100)
    ap.add_argument("--audio-offset", type=int, default=0, help="ms; -earlier")
    ap.add_argument("--bg-dim-intro", type=int, default=0)
    ap.add_argument("--bg-dim-game", type=int, default=90)
    ap.add_argument("--bg-dim-breaks", type=int, default=30)
    ap.add_argument("--bg-dim", type=int, default=None,
                    help="override --bg-dim-game (gameplay dim, 0-100)")
    ap.add_argument("--bg-blur", type=int, default=0,
                    help="§4.10 Blur 0-10: gaussian on the bg at load")
    ap.add_argument("--bg-parallax", action=BA, default=False,
                    help="§4.10 Parallax: bg ~1.02×, sliding opposite "
                         "the cursor")
    ap.add_argument("--bg-triangles", action=BA, default=False,
                    help="the osu triangles deco drifting up over the bg")
    ap.add_argument("--storyboard", action=BA, default=False,
                    help="§4.10 LoadStoryboards: ACCEPTED + NO-OP — "
                         "storyboards are a deferred subsystem (the "
                         "service keeps its danser fallback for SB maps)")
    ap.add_argument("--video", action=BA, default=False,
                    help="§4.10 LoadVideos: play the map's [Events] Video "
                         "behind gameplay (fail-soft to the bg image)")
    ap.add_argument("--flash-to-beat", action=BA, default=False,
                    help="§4.10 FlashToTheBeat: subtle bg brightness "
                         "pulse on each red-line beat")
    ap.add_argument("--seizure-warning", action=BA, default=False,
                    help="§4.10 SeizureWarning: 5 s dark warning card "
                         "pre-roll at render start")
    ap.add_argument("--bloom", action=BA, default=False,
                    help="§4.10 Bloom: post-process bloom on the gameplay "
                         "layer (bright-pass + blur + additive; GPU cost)")
    ap.add_argument("--bloom-to-beat", action=BA, default=True,
                    help="pulse the bloom strength on beats (with --bloom)")
    ap.add_argument("--logo", action=BA, default=False,
                    help="show_logo: the R3D 'R' tile splash during the "
                         "intro, fading out as gameplay starts")
    ap.add_argument("--combo-colors", choices=("skin", "beatmap"),
                    default="skin",
                    help="skin_combo_colors preset key: 'skin' = skin.ini "
                         "Combo1.. (default); 'beatmap' = the .osu "
                         "[Colours] when the map defines them")
    ap.add_argument("--results-seconds", type=float, default=None)
    ap.add_argument("--no-replay", action="store_true",
                    help="render without a replay: perfect play at object "
                         "times, auto-spun spinners (§2.5 RPMS); pass the "
                         "beatmap as the only positional (debug: no "
                         "cursor/HUD/judgments — and thus no hitsounds)")
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
    ap.add_argument("--add-mods", type=_mod_list, default=0, metavar="HD,FL",
                    help="force extra VISUAL mods (HD/FL) on top of the "
                         "replay's mods — for proofs when no replay has them")
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


# the R3D render DB — read-only source for the LOCAL-ONLY PB card (owner
# decision: no osu!API). Overridable for tests/deployments.
PB_DB_PATH = os.environ.get("R3D_RENDER_DB",
                            "/home/red/.local/state/mania-ordr/db.sqlite")
LAZER_RESULTS_MIN_SECONDS = 4.5   # floor so both stages + a hold fit


def _build_lazer_results(spr, settings, beatmap, meta, judgments, hud, fv,
                         frames, osu_path, args, speed, frozen=None):
    """Assemble the lazer ranking screen (render/lazer_results.py) — gather
    stars/pp/perf-breakdown/slider-stats/aim-scatter/PB-card data, size the
    outro to fit both stages. Returns (screen, duration_wall_ms).

    `frozen` (a FAIL): a dict with grade/counts/max_combo/acc_pct/score
    tallied AT the death point — overrides the .osr's full-map totals, and
    pp/perf are dropped (meaningless for a partial play)."""
    from .render.hud import build_aim_points
    from .render.lazer_results import (LazerResultsScreen, ResultsData,
                                       query_pb, slider_stats)
    from .render.pp import build_performance_breakdown, star_rating

    is_fail = frozen is not None
    if is_fail:
        counts = frozen["counts"]
        grade = frozen["grade"]
        acc_pct = frozen["acc_pct"]
        score = frozen["score"]
        max_combo = frozen["max_combo"]
    else:
        counts = (meta.count_300, meta.count_100, meta.count_50,
                  meta.count_miss)
        grade = meta.grade
        acc_pct = meta.accuracy
        score = int(meta.score or fv["score"])
        max_combo = meta.max_combo
    stars = star_rating(osu_path, meta.mods)
    # pp/perf are pass-only: a failed play never earns pp
    if is_fail:
        perf = None
    else:
        perf = build_performance_breakdown(osu_path, meta.mods, judgments,
                                           counts, judgments.final_max_combo)
    pp_val = perf.achieved_pp if perf is not None else None
    tick_hit, tick_total, end_hit, end_total = slider_stats(
        judgments, before=frozen["fail_time"] if is_fail else None)
    aim_points = build_aim_points(judgments, frames,
                                  beatmap.diff.circle_radius)
    # PB card: this player's best PREVIOUS render of the map (exclude the
    # current replay by its md5), local DB, read-only. None → card omitted.
    replay_md5 = ""
    try:
        if args.osr is not None:
            replay_md5 = hashlib.md5(Path(args.osr).read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001
        replay_md5 = ""
    pb = query_pb(PB_DB_PATH, meta.player_name, meta.beatmap_md5, replay_md5)
    if pb is not None:
        print(f"pb:     {pb['player_name']} {int(pb['score']):,} "
              f"{pb['grade']} (render DB) — PB card shown", file=sys.stderr)
    else:
        print("pb:     no prior render of this map for the player — PB card "
              "omitted", file=sys.stderr)

    data = ResultsData(
        player=meta.player_name, grade=grade, acc_pct=acc_pct,
        score=score, max_combo=max_combo,
        counts=counts, title=beatmap.name, artist=beatmap.artist,
        diff_name=beatmap.difficulty_name, creator=beatmap.creator,
        mods=meta.mods, stars=stars, pp=pp_val,
        date_str=(meta.played_at or "—"), ur=fv["ur"],
        slider_ticks=(tick_hit, tick_total),
        slider_ends=(end_hit, end_total),
        err_deltas=list(hud.data.err_deltas),
        windows=(hud.hw.great, hud.hw.ok, hud.hw.meh),
        aim_points=aim_points, perf=perf, pb=pb)
    dur_wall_ms = max(settings.results_screen_time,
                      LAZER_RESULTS_MIN_SECONDS) * 1000.0
    screen = LazerResultsScreen(spr, data, dur_wall_ms, speed=speed,
                                argon_font=settings.skin_dir is None)
    return screen, dur_wall_ms


def _render(args, settings: StdRenderSettings, beatmap, frames,
            beatmap_dir: Path, skin_info, judgments=None, meta=None,
            osu_path: Path | None = None) -> int:
    """The Phase-1 record path: scene → record loop → ffmpeg."""
    # GL-touching imports live here so --parse-only works GL-less
    from .record.audio import AudioError, AudioMixer, decode_to_pcm
    from .record.encode import FfmpegPipe, build_ffmpeg_cmd, probe_encoder
    from .record.pipeline import RecordPipeline
    from .render.background import (blur_background, build_dim_envelope,
                                    cover_size, load_background)
    from .render.bloom import BloomPass
    from .render.effects import SEIZURE_DURATION_S
    from .render.gl import SpriteRenderer
    from .render.hud import StdHud, build_aim_points
    from .render.playfield import PlayfieldCamera
    from .render.scene import FAIL_DURATION_MS, ScenePlayer, StdScene
    from .render.skin_elements import SkinElements
    from .render.slider_body import SliderBodyRenderer
    from .render.textures import TextureBank
    from .render.video_bg import VideoBackground
    from .skin.skin import Skin

    w, h = settings.resolution
    spr = SpriteRenderer(w, h)
    bank = TextureBank(spr)
    bodies = SliderBodyRenderer(spr.ctx, w, h)
    cam = PlayfieldCamera(w, h)

    # FAIL: the death point in MAP ms (None = pass or --no-fail-animation).
    # Gameplay freezes here, the fail sequence plays, then the F results.
    fail_time = (meta.fail_time if meta is not None
                 and not args.no_fail_animation else None)

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
            if settings.bg_blur > 0:      # §4.10 Blur — once, at load
                rgba = blur_background(rgba, settings.bg_blur)
            spr.upload_texture("background", rgba)
            bg_key = "background"
            bg_size = cover_size(w, h, rgba.shape[1], rgba.shape[0])
        else:
            print(f"WARNING: background '{beatmap.bg}' failed to decode — "
                  "rendering without background", file=sys.stderr)
    elif beatmap.bg:
        print(f"WARNING: background '{beatmap.bg}' not found in the beatmap "
              "dir — rendering without background", file=sys.stderr)

    # dim envelope applies to image AND video backgrounds
    dim_env = build_dim_envelope(
        settings.bg_dim_intro / 100.0, settings.bg_dim_game / 100.0,
        settings.bg_dim_breaks / 100.0,
        [o.get_start_time() for o in beatmap.hit_objects],
        beatmap.diff.preempt, beatmap.pauses)

    # --- §4.10 LoadVideos (render/video_bg.py — the mania v2 port) ----------------
    video_bg = None
    if settings.load_video:
        if beatmap.video:
            vpath = beatmap.get_related_file(beatmap_dir, beatmap.video)
            if vpath is not None:
                video_bg = VideoBackground(vpath, width=w, height=h,
                                           fps=settings.fps,
                                           start_ms=beatmap.video_offset)
                print(f"video:  {beatmap.video} from "
                      f"{beatmap.video_offset} ms", file=sys.stderr)
            else:
                print(f"WARNING: video '{beatmap.video}' not found in the "
                      "beatmap dir — static background", file=sys.stderr)
        else:
            print("note: load_video on, but the map has no [Events] Video "
                  "— static background", file=sys.stderr)

    # the HUD needs the judgment stream; --dump-frames without a replay
    # sim just renders HUD-less (the Phase-1 fallback). Component set per
    # the hud.py hybrid rule: per element, the skin's legacy component
    # where the skin ships it, Argon otherwise (legacy_defaults → all
    # legacy). The HP drain model (ruleset/health.py) and the .osr score
    # pin wire in here.
    hud = None
    if judgments is not None:
        from .ruleset import HealthTimeline
        health = HealthTimeline(beatmap, judgments)
        print(f"health: drain {health.drain_rate * 1000.0:.3f} hp/s "
              f"(target min {health.target_min:.2f}), "
              f"end {health.final_hp * 100.0:.0f}%", file=sys.stderr)

        # --- §4.6 pp counter / strain graph / aim error data (fail-soft) --
        pp_timeline = strain = None
        aim_points = None
        if settings.show_pp_counter:
            from .render.pp import build_pp_timeline
            res = build_pp_timeline(osu_path, meta.mods if meta else 0,
                                    judgments)
            if res is not None:
                pp_timeline, ppinfo = res
                delta = abs(ppinfo.gradual_end - ppinfo.full_calc)
                ok = "==" if delta < 0.05 else f"Δ{delta:.2f}"
                print(f"pp:     gradual end {ppinfo.gradual_end:.2f}pp "
                      f"(full calc {ppinfo.full_calc:.2f}pp {ok}, "
                      f"{ppinfo.n_points} points)", file=sys.stderr)
            else:
                print("pp:     unavailable — counter hidden (rosu-pp "
                      "missing or the map failed)", file=sys.stderr)
        if settings.show_strain_graph:
            from .render.pp import build_strain_series, strain_proxy
            starts = [o.get_start_time() for o in beatmap.hit_objects]
            ends = [o.get_end_time() for o in beatmap.hit_objects]
            strain = build_strain_series(osu_path,
                                         meta.mods if meta else 0,
                                         beatmap.diff.speed,
                                         min(starts) if starts else 0.0)
            if strain is None:
                strain = strain_proxy(starts, ends)
            if strain is not None:
                print(f"strain: {len(strain.values)} sections via "
                      f"{strain.source}", file=sys.stderr)
        if settings.show_aim_error_meter:
            aim_points = build_aim_points(judgments, frames,
                                          beatmap.diff.circle_radius)

        hud = StdHud(spr, bank, settings, judgments, frames, beatmap,
                     skin_elems=skin_elems, health=health,
                     mods=meta.mods if meta is not None else 0,
                     pp_timeline=pp_timeline, aim_points=aim_points,
                     strain=strain)
        if meta is not None and meta.score > 0 and fail_time is None:
            # pin the displayed score curve to the .osr's recorded total
            # (PASS only — a fail's .osr score is the partial death tally,
            # and the HUD is frozen at fail_time showing that tally already)
            hud.pin_final_score(meta.score)

    # --- RED'S results screen (render/results.py; §4.6 ShowResultsScreen) --------
    # fade_out_time is WALL seconds — × speed for map-ms (the same rate-mod
    # convention as results_screen_time; the pre-fix code forgot the ×speed)
    last_end = max(o.get_end_time() for o in beatmap.hit_objects)
    speed = beatmap.diff.speed
    fade_start_ms = last_end + HIT_FADE_OUT
    fade_len_ms = settings.fade_out_time * 1000.0 * speed
    gameplay_end_ms = fade_start_ms + fade_len_ms
    # FAIL: the sequence lasts FailAnimation.duration=2500 ms WALL → ×speed
    fail_anim_len_ms = FAIL_DURATION_MS * speed

    # FAIL results: grade F + stats FROZEN at the death point (NOT the .osr's
    # full-map reconciled totals). Tally judgments up to fail_time from the
    # (un-reconciled) sim, peak combo up to death, accuracy from those counts.
    frozen = None
    if fail_time is not None and hud is not None:
        fc = hud.data.counts_at(fail_time)
        ftot = sum(fc)
        facc = ((300 * fc[0] + 100 * fc[1] + 50 * fc[2]) / (300.0 * ftot)
                if ftot else 1.0)
        frozen = {
            "grade": "F",
            "counts": fc,
            "max_combo": hud.data.max_combo_upto(fail_time),
            "acc_pct": round(facc * 100.0, 2),
            "score": int(meta.score) if meta.score > 0
            else hud.data.score_upto(fail_time),
            "fail_time": fail_time,
        }
        print(f"fail:   frozen stats @ death — {fc[0]}/{fc[1]}/{fc[2]}/{fc[3]} "
              f"{frozen['acc_pct']}% combo {frozen['max_combo']}x "
              f"score {frozen['score']} grade F", file=sys.stderr)

    results = results_start_ms = results_dur_wall_ms = None
    if settings.show_results and hud is not None and meta is not None:
        fv = hud.final_values()
        deltas = hud.data.err_deltas
        avg_ms = (sum(deltas) / len(deltas)) if deltas else 0.0
        if settings.results_style == "lazer":
            results, results_dur_wall_ms = _build_lazer_results(
                spr, settings, beatmap, meta, judgments, hud, fv, frames,
                osu_path, args, speed, frozen=frozen)
        else:
            from .render.results import ResultsScreen
            r_counts = frozen["counts"] if frozen else (
                meta.count_300, meta.count_100, meta.count_50, meta.count_miss)
            r_grade = frozen["grade"] if frozen else meta.grade
            r_acc = frozen["acc_pct"] if frozen else meta.accuracy
            r_score = frozen["score"] if frozen else (meta.score or fv["score"])
            r_combo = frozen["max_combo"] if frozen else meta.max_combo
            results = ResultsScreen(
                spr, skin_elems,
                counts=r_counts,
                acc_pct=r_acc, score=r_score,
                max_combo=r_combo, grade=r_grade, ur=fv["ur"],
                avg_ms=avg_ms, err_deltas=deltas, meh_ms=hud.hw.meh,
                player=meta.player_name,
                map_line=f"{beatmap.artist} - {beatmap.name}",
                diff_name=beatmap.difficulty_name, mods=meta.mods,
                use_skin_ranks=not settings.renderer_default_font_and_ranks,
                argon_font=settings.skin_dir is None)
            results.set_windows(hud.hw.great, hud.hw.ok)
            results_dur_wall_ms = settings.results_screen_time * 1000.0
        # PASS → results after the map-end fade; FAIL → after the fall
        results_start_ms = (fail_time + fail_anim_len_ms
                            if fail_time is not None else gameplay_end_ms)

    # §3.5/§4.7 combo colour source: the .osu [Colours] when the preset
    # says "beatmap" AND the map defines them; the skin.ini set otherwise
    color_src = skin_info.combo_colors
    combo_colors_from_beatmap = False
    if settings.use_beatmap_colors and beatmap.combo_colors:
        color_src = beatmap.combo_colors
        combo_colors_from_beatmap = True
        print(f"colors: beatmap [Colours] ({len(color_src)} combo colours)",
              file=sys.stderr)
    combo_colors = [(r / 255.0, g / 255.0, b / 255.0)
                    for r, g, b in color_src]
    track_override = None
    if skin_info.slider_track_override is not None:
        track_override = tuple(c / 255.0
                               for c in skin_info.slider_track_override)

    # --- flow: end/start + the §4.10 pre-roll (lead-in + seizure card) ------------
    # FAIL: gameplay ends at the death frame + the fall, never at map end
    end_ms = (fail_time + fail_anim_len_ms if fail_time is not None
              else gameplay_end_ms)
    if results is not None:
        # results duration is WALL ms — scale by the rate mod so the outro
        # holds the same real time under DT/HT (the lazer style floors the
        # duration so both stages + a hold always fit — see _build_lazer)
        end_ms = results_start_ms + results_dur_wall_ms * speed
    if args.max_seconds is not None:
        end_ms = min(end_ms, args.max_seconds * 1000.0)
    start_ms = (args.start or 0.0) * 1000.0
    if start_ms and start_ms >= end_ms:
        print(f"error: --start {args.start:g}s is at/after the render end "
              f"({end_ms / 1000.0:.1f}s)", file=sys.stderr)
        return 2
    # lead_in_time and the seizure card are WALL seconds of extra pre-roll
    # BEFORE start_ms: the map clock simply begins earlier — objects
    # can't spawn, the dim envelope holds the intro level, music is laid
    # correspondingly later into the wall timeline
    seizure_ms = (SEIZURE_DURATION_S * 1000.0 * speed
                  if settings.seizure_warning else 0.0)
    lead_ms = settings.lead_in_time * 1000.0 * speed
    render_start_ms = start_ms - seizure_ms - lead_ms
    if seizure_ms or lead_ms:
        print(f"lead:   {(seizure_ms + lead_ms) / speed / 1000.0:.1f}s "
              f"pre-roll (seizure {seizure_ms / speed / 1000.0:.1f}s + "
              f"lead-in {lead_ms / speed / 1000.0:.1f}s)", file=sys.stderr)

    bloom_pass = BloomPass(spr.ctx, w, h) if settings.bloom else None

    # mods driving the playfield visuals (HD fades / FL overlay): the
    # replay's mods plus any forced visual-only --add-mods
    scene_mods = (meta.mods if meta is not None else 0) | getattr(
        args, "add_mods", 0)

    scene = StdScene(
        beatmap, frames, cam, spr, bodies, bank,
        combo_colors=combo_colors,
        combo_colors_from_beatmap=combo_colors_from_beatmap,
        snaking_in=settings.slider_snaking_in,
        snaking_out=settings.slider_snaking_out,
        slider_merge=settings.slider_merge,
        draw_approach_circles=settings.draw_approach_circles,
        draw_combo_numbers=settings.draw_combo_numbers,
        draw_follow_points=settings.draw_follow_points,
        draw_cursor=settings.draw_cursor,
        cursor_scale=settings.cursor_scale,
        cursor_trail_scale=settings.cursor_trail_scale,
        cursor_rainbow=settings.cursor_rainbow,
        cursor_ripples=settings.cursor_ripples,
        force_long_trail=settings.cursor_long_trail,
        judgments=judgments,
        draw_hit_lighting=settings.show_hit_lighting,
        spinner_fade_playfield=skin_info.spinner_fade_playfield,
        spinner_no_blink=skin_info.spinner_no_blink,
        hud=hud,
        skin_elems=skin_elems,
        use_skin_cursor=settings.use_skin_cursor,
        border_color=tuple(c / 255.0 for c in skin_info.slider_border),
        track_override=track_override,
        bg_key=bg_key,
        bg_draw_size=bg_size,
        dim_envelope=dim_env,
        video_bg=video_bg,
        bg_parallax=settings.bg_parallax,
        bg_triangles=settings.bg_triangles,
        flash_to_beat=settings.flash_to_beat,
        show_warning_arrows=settings.show_warning_arrows,
        fade_start_ms=fade_start_ms,
        fade_len_ms=fade_len_ms,
        logo_start_ms=(render_start_ms + seizure_ms
                       if settings.show_logo else None),
        seizure_start_ms=(render_start_ms if settings.seizure_warning
                          else None),
        bloom_pass=bloom_pass,
        bloom_to_beat=settings.bloom_to_beat,
        miss_fall=settings.miss_fall,
        playfield_borders=settings.playfield_borders,
        results=results,
        results_start_ms=results_start_ms,
        mods=scene_mods,
        fail_time_ms=fail_time,
        fail_anim_len_ms=fail_anim_len_ms,
    )

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
        _print_hud_final_values(hud, judgments, meta, frozen=frozen)
        if video_bg is not None:
            video_bg.close()
        spr.release()
        return 0

    if args.output is None:
        print("error: -o/--output is required to render", file=sys.stderr)
        return 2
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    # --- offline audio: music bed + §3.4 hitsounds (NO-BASS design) ---------------
    audio_path = None
    mixer = AudioMixer((end_ms - render_start_ms) / speed)
    have_audio = False
    afile = beatmap.get_audio_file(beatmap_dir)
    if afile is not None:
        try:
            pcm = decode_to_pcm(afile, rate=speed)
            vol = ((settings.music_volume / 100.0)
                   * (settings.general_volume / 100.0))
            # the map-time render start lands at wall t=0: the (already
            # rate-adjusted) music is laid render_start/speed early —
            # mix_at clips a negative head; a pre-roll delays it instead
            mixer.lay_music(pcm, -render_start_ms / speed, volume=vol)
            have_audio = True
        except AudioError as e:
            print(f"WARNING: music decode failed, mixing without the music "
                  f"bed: {e}", file=sys.stderr)
    else:
        print(f"WARNING: beatmap audio '{beatmap.audio}' not found — "
              "mixing without the music bed", file=sys.stderr)

    # sample bank shared by judged hitsounds + the nightcore overlay;
    # the synth-default bank follows the visual league (skinless→argon,
    # custom-skin gaps→legacy, --legacy-defaults→legacy)
    sample_bank = None
    if settings.hitsound_volume > 0 and settings.general_volume > 0 and (
            (settings.use_replay_hitsounds and judgments is not None)
            or settings.nightcore_hitsounds):
        from .record.hitsounds import SampleBank, synth_style_for
        from .skin.skin import Skin as SampleSkin
        sample_skin = SampleSkin(skin_dir=settings.skin_dir,
                                 fallback_dir=settings.default_skin_dir)
        sample_bank = SampleBank(
            skin=sample_skin, beatmap_dir=beatmap_dir,
            use_beatmap_samples=not settings.use_skin_hitsounds,
            synth_style=synth_style_for(settings.skin_dir is not None,
                                        settings.legacy_defaults))
    hs_gain = ((settings.hitsound_volume / 100.0)
               * (settings.general_volume / 100.0))

    # §3.4 hitsounds: one-shots at judged hit times + slide/spin loops,
    # resolved BEATMAP(custom index) → skin chain → synthesized defaults
    if (settings.use_replay_hitsounds and judgments is not None
            and sample_bank is not None):
        from .record.hitsounds import collect_hitsound_events, mix_hitsounds
        oneshots, loops = collect_hitsound_events(
            beatmap, judgments, layered=skin_info.layered_hit_sounds)
        if fail_time is not None:
            # FAIL: objects after the death point were never played — drop
            # their hitsounds (loops clip at the death point)
            oneshots = [o for o in oneshots if o.time_ms < fail_time]
            loops = [l for l in loops if l.t0 < fail_time]
        stats = mix_hitsounds(mixer, sample_bank, oneshots, loops,
                              speed=speed, start_ms=render_start_ms,
                              gain=hs_gain)
        srcs = sample_bank.source_counts()
        print(f"hitsounds: {stats.oneshots} one-shots, "
              f"{stats.loop_ms / 1000.0:.1f}s loops | samples: "
              f"beatmap {srcs['beatmap']}, skin {srcs['skin']}, "
              f"synth {srcs['synth']} ({sample_bank.synth_style} bank) | "
              f"track peak "
              f"{stats.peak_before:.2f}→{stats.peak_after:.2f}",
              file=sys.stderr)
        have_audio = have_audio or stats.oneshots > 0 or stats.loop_ms > 0

    # §4.4 nightcore beat overlay: clap each beat + finish each downbeat
    # across [render start, last object] — the mania _layer_nightcore
    # mirror (works with or without the judged hitsound track)
    if settings.nightcore_hitsounds and sample_bank is not None:
        from .record.hitsounds import mix_nightcore, nightcore_beats
        beats = nightcore_beats(beatmap.timings,
                                max(render_start_ms, 0.0), last_end)
        laid = mix_nightcore(mixer, sample_bank, beats, speed=speed,
                             start_ms=render_start_ms, gain=hs_gain)
        downs = sum(1 for _, d in beats if d)
        print(f"nightcore: {laid} beats laid ({downs} downbeats)",
              file=sys.stderr)
        have_audio = have_audio or laid > 0

    # §4.10 pre-roll audio: the seizure card / lead-in region is SILENT
    # (danser LeadInTime semantics) — for a map-start render the region
    # is silent anyway; this also covers --start clips with a pre-roll
    if have_audio and (seizure_ms or lead_ms):
        mixer.silence_before((start_ms - render_start_ms) / speed)

    # §4.10 FadeOutTime, audio side: the track fades with the video
    if have_audio and fade_len_ms > 0.0 and fail_time is None:
        mixer.fade_out((fade_start_ms - render_start_ms) / speed,
                       (gameplay_end_ms - render_start_ms) / speed)

    # FAIL audio: the FailAnimation bends the track frequency to 0 over the
    # 2500 ms fall (a slowdown + pitch drop). We can't pitch-bend offline
    # without BASS, so we APPROXIMATE with a linear music fade to silence
    # across the fall (honest gap: no pitch-bend), and play the synthesized
    # fail sample once at the death point (FailAnimation.failSample.Play).
    if fail_time is not None and settings.general_volume > 0:
        from .record.hitsounds import synth_failsound
        t0 = (fail_time - render_start_ms) / speed
        t1 = (fail_time + fail_anim_len_ms - render_start_ms) / speed
        if have_audio:
            mixer.fade_out(t0, t1)
        fs_vol = settings.general_volume / 100.0
        mixer.mix_at(t0, synth_failsound(), volume=fs_vol)
        have_audio = True
        print(f"fail:   music fades {t0 / 1000.0:.1f}→{t1 / 1000.0:.1f}s "
              f"(wall) + fail sample at {t0 / 1000.0:.1f}s "
              f"(pitch-bend approximated)", file=sys.stderr)

    if have_audio:
        audio_path = output.with_suffix(".audio.wav")
        mixer.write_wav(audio_path)
    else:
        print("WARNING: no audio mixed — rendering SILENT video",
              file=sys.stderr)

    encoder = probe_encoder(settings.encoder)
    cmd = build_ffmpeg_cmd(
        encoder=encoder, resolution=(w, h), fps=settings.fps,
        output_path=output, audio_path=audio_path,
        audio_offset_ms=settings.audio_offset)

    total_wall_ms = (end_ms - render_start_ms) / speed
    last_pct = [-1]

    def progress(frac: float) -> None:
        pct = int(frac * 100)
        if pct != last_pct[0]:
            last_pct[0] = pct
            print(f"rendering… {pct}%", file=sys.stderr, flush=True)

    player = ScenePlayer(scene, end_ms, speed=speed,
                         start_ms=render_start_ms)
    t0 = time.monotonic()
    try:
        with FfmpegPipe(cmd) as pipe:
            n_frames = RecordPipeline(settings.fps, pipe.push,
                                      progress=progress).run(
                player, total_ms=total_wall_ms)
    finally:
        if video_bg is not None:
            video_bg.close()
        if audio_path is not None:
            try:
                audio_path.unlink()
            except OSError:
                pass
    wall = time.monotonic() - t0
    _print_hud_final_values(hud, judgments, meta, frozen=frozen)
    print(f"done: {n_frames} frames in {wall:.1f}s "
          f"({n_frames / wall:.1f} fps render, encoder {encoder}) → {output}",
          file=sys.stderr)
    spr.release()
    return 0


def _print_hud_final_values(hud, judgments, meta=None, frozen=None) -> None:
    """The HUD phase's honesty line: the numbers the LAST frame displays,
    with the sim-vs-replay score/accuracy checks spelled out.

    A FAILED render freezes the HUD at the death point, so the whole-map
    end values (all post-death misses) are meaningless and the replay
    comparison would false-alarm — report the frozen death tally instead."""
    if hud is None:
        return
    if frozen is not None:
        c3, c1, c5, cm = frozen["counts"]
        print(f"hud[FAIL]: frozen @ death — score {frozen['score']} | "
              f"acc {frozen['acc_pct']:.2f}% | {c3}/{c1}/{c5}/{cm} | "
              f"max combo {frozen['max_combo']}x | grade F", file=sys.stderr)
        return
    fv = hud.final_values()
    score_line = f"final score {fv['score']}"
    if meta is not None and meta.score > 0:
        ok = "==" if fv["score"] == meta.score else "!= MISMATCH"
        score_line += f" (replay {meta.score} {ok})"
    acc_line = f"acc {fv['acc'] * 100.0:.2f}%"
    if judgments is not None and judgments.real_counts is not None:
        c3, c1, c5, cm = judgments.real_counts
        total = c3 + c1 + c5 + cm
        real_acc = ((300 * c3 + 100 * c1 + 50 * c5) / (300.0 * total)
                    if total else 1.0)
        ok = "==" if abs(real_acc - fv["acc"]) < 5e-5 else "!= MISMATCH"
        acc_line += f" (replay {real_acc * 100.0:.2f}% {ok})"
    hp_part = ""
    if fv.get("hp") is not None:
        hp_part = f" | hp {fv['hp'] * 100.0:.0f}%"
    print(f"hud: {score_line} | {acc_line} | "
          f"combo {fv['combo']}x (max {fv['max_combo']}x) | "
          f"UR {fv['ur']:.1f} | grade {fv['grade']}{hp_part}",
          file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --no-replay: the beatmap may be the only positional
    if args.no_replay and args.beatmap is None:
        args.osr, args.beatmap = None, args.osr
    if args.beatmap is None or (args.osr is None and not args.no_replay):
        print("error: expected REPLAY.osr BEATMAP (or --no-replay BEATMAP)",
              file=sys.stderr)
        return 2

    settings = StdRenderSettings(
        resolution=args.resolution, fps=args.fps, encoder=args.encoder,
        encoder_device=args.encoder_device, skin_dir=args.skin,
        default_skin_dir=args.default_skin, skip_intro=args.skip_intro,
        lead_in_time=max(args.lead_in, 0.0),
        show_results=args.results, results_style=args.results_style,
        letterbox_breaks=args.letterbox_breaks,
        draw_approach_circles=args.approach_circles,
        draw_combo_numbers=args.combo_numbers,
        draw_follow_points=args.follow_points,
        slider_snaking_in=args.snaking_in, slider_snaking_out=args.snaking_out,
        slider_merge=args.slider_merge,
        draw_cursor=args.cursor, use_skin_cursor=args.skin_cursor,
        cursor_scale=args.cursor_scale,
        cursor_trail_scale=args.cursor_trail_scale,
        cursor_rainbow=args.cursor_rainbow,
        cursor_ripples=args.cursor_ripples,
        cursor_long_trail=args.force_long_trail,
        show_key_overlay=args.key_overlay,
        show_pp_counter=args.pp_counter, show_hit_counter=args.hit_counter,
        show_aim_error_meter=args.aim_error_meter,
        show_strain_graph=args.strain_graph,
        show_scoreboard=args.scoreboard,
        scoreboard_avatars=args.scoreboard_avatars,
        show_warning_arrows=args.warning_arrows,
        show_hit_error_meter=args.hit_error_meter,
        show_unstable_rate=args.unstable_rate, show_combo=args.show_combo,
        show_score=args.show_score, show_hp_bar=args.show_hp,
        show_grade=args.show_grade, show_mods=args.show_mods,
        show_progress=args.show_progress, progress_style=args.progress_style,
        hud_scale=args.hud_scale, hud_opacity=args.hud_opacity,
        combo_break_flash=args.break_flash,
        show_hit_lighting=args.hit_lighting,
        miss_fall=args.miss_fall,
        renderer_default_font_and_ranks=args.renderer_default_font_and_ranks,
        legacy_defaults=args.legacy_defaults,
        playfield_borders=args.playfield_borders,
        watermark_text=args.watermark, music_volume=args.music_volume,
        hitsound_volume=args.hitsound_volume,
        use_replay_hitsounds=args.hitsounds,
        nightcore_hitsounds=args.nightcore_hitsounds,
        use_skin_hitsounds=args.use_skin_hitsounds,
        general_volume=args.general_volume, audio_offset=args.audio_offset,
        bg_dim_intro=args.bg_dim_intro,
        bg_dim_game=(args.bg_dim if args.bg_dim is not None
                     else args.bg_dim_game),
        bg_dim_breaks=args.bg_dim_breaks, bg_blur=args.bg_blur,
        bg_parallax=args.bg_parallax, bg_triangles=args.bg_triangles,
        load_storyboard=args.storyboard, load_video=args.video,
        flash_to_beat=args.flash_to_beat,
        seizure_warning=args.seizure_warning,
        bloom=args.bloom, bloom_to_beat=args.bloom_to_beat,
        show_logo=args.logo,
        use_beatmap_colors=(args.combo_colors == "beatmap"),
        skin_combo_colors=(args.combo_colors != "beatmap"),
    )
    if args.fade_out is not None:
        settings.fade_out_time = max(args.fade_out, 0.0)
    if args.results_seconds is not None:
        settings.results_screen_time = args.results_seconds
    if settings.load_storyboard:
        print("note: load_storyboard is accepted but NOT rendered "
              "(deferred subsystem — the service keeps its danser "
              "fallback for storyboard maps)", file=sys.stderr)

    if args.no_replay:
        frames, meta = [], None
        osu_path = find_osu_file(args.beatmap, "")
        beatmap = load_full(osu_path, mods=0)
    else:
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
    if meta is not None:
        print(f"replay: {meta.player_name} {meta.count_300}/{meta.count_100}/"
              f"{meta.count_50}/{meta.count_miss} {meta.accuracy}% {meta.grade} "
              f"mods={meta.mods:#x} frames={len(frames)}", file=sys.stderr)
    else:
        print("replay: (none — --no-replay perfect play)", file=sys.stderr)
    print(f"skin:   \"{skin_info.name or 'default'}\" v{skin_info.version:g}",
          file=sys.stderr)

    # judgment simulation (pure CPU — runs in --parse-only too, so the
    # sim-vs-real honesty metric is checkable without GL)
    # a FAILED replay (life-bar hit 0, no fail-immune mod) renders osu's fail
    # sequence and its counts are the partial tally AT death — so the ruleset
    # must NOT reconcile the whole-map sim to the .osr's (partial) totals.
    failing = (meta is not None and meta.fail_time is not None
               and not args.no_fail_animation)
    judgments = None
    if frames and meta is not None and meta.mode == 0:
        from .ruleset import StdRuleset
        judgments = StdRuleset(beatmap, frames, meta,
                               reconcile=not failing).run()
        for line in judgments.report_lines():
            print(line, file=sys.stderr)
    if meta is not None and meta.fail_time is not None:
        if failing:
            print(f"fail:   life-bar hit 0 at {meta.fail_time / 1000.0:.2f}s "
                  f"— rendering osu fail sequence (grade F)", file=sys.stderr)
        else:
            print(f"fail:   life-bar hit 0 at {meta.fail_time / 1000.0:.2f}s "
                  f"but --no-fail-animation set — rendering as a pass",
                  file=sys.stderr)

    if args.parse_only:
        return 0

    if meta is not None and meta.mode != 0:
        print(f"error: replay mode {meta.mode} is not osu!standard",
              file=sys.stderr)
        return 2
    if not frames and not args.no_replay:
        print("error: replay has no cursor frames", file=sys.stderr)
        return 2

    # §4.15 -skip semantics: trim a long SILENT intro — start ~1 s before
    # the first object's approach begins, but only when that actually
    # saves more than 2 s (short intros render untouched) and no explicit
    # --start was given. --dump-frames uses absolute map times either way.
    if args.skip_intro and args.start is None and beatmap.hit_objects:
        first_t = min(o.get_start_time() for o in beatmap.hit_objects)
        t0 = (first_t - beatmap.diff.preempt - 1000.0) / 1000.0
        if t0 > 2.0:
            args.start = t0
            print(f"skip:   intro trimmed to {t0:.1f}s (first object at "
                  f"{first_t / 1000.0:.1f}s)", file=sys.stderr)

    beatmap_dir = args.beatmap if args.beatmap.is_dir() else args.beatmap.parent
    return _render(args, settings, beatmap, frames, beatmap_dir, skin_info,
                   judgments=judgments, meta=meta, osu_path=osu_path)


if __name__ == "__main__":
    raise SystemExit(main())
