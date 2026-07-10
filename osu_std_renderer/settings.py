"""Render settings — RENDER_PLAN.md §4 mapped onto R3D's preset keys.

The reference exposes a huge settings JSON (§4.2–§4.14). The service only
surfaces the subset in mania_ordr/presets.py (`_apply_preset_to_danser_cfg`
is the parity checklist, APPROACH.md §2.5); this dataclass is that subset,
named to match the R3D PRESET KEYS 1:1 so the std_renderer.py adapter can
map preset dict → CLI flags mechanically (same pattern as catch).

`from_preset()` is THE adapter entry: it consumes the bot's preset JSON
(presets.validate() output) WHOLESALE — unknown keys (mania/catch-only,
service-level) are ignored, value-shape differences (resolution string,
encoder nicknames, skin_combo_colors "beatmap"/"skin", the show_boundaries
bool) are converted here so the future std_renderer.py service adapter is a
two-liner.

Mapping table (preset key ↔ reference §4 setting):

  R3D preset key            reference setting (§4)
  ------------------------  -------------------------------------------
  resolution / fps          Recording.FrameWidth/FrameHeight/FPS   §4.13
  encoder                   Recording.Encoder ("nvenc"/"vaapi"/
                            "x264" nicknames mapped to ffmpeg names) §4.13
  skin / use_replay_skin    Skin.CurrentSkin (resolver-extracted;
                            use_replay_skin is SERVICE-side)        §4.7
  skin_combo_colors         "skin" → skin.ini Combo1.. wins;
                            "beatmap" → the .osu [Colours] wins     §4.7/§3.5
  music_volume              Audio.MusicVolume                      §4.4
  hitsound_volume           Audio.SampleVolume                     §4.4
  general_volume            Audio.GeneralVolume                    §4.4
  audio_offset              -offset CLI (Audio.Offset is IGNORED
                            in recordings — §4.4 footgun)          §4.15
  use_replay_hitsounds      the judged-hit hitsound track on/off
                            (hitsounds always fire at REAL press
                            times here — the mania semantic)       §3.4
  nightcore_hitsounds       Audio.PlayNightcoreSamples — clap each
                            beat + finish each bar downbeat        §4.4
  use_skin_hitsounds        Audio.IgnoreBeatmapSamples (inverted)  §4.4
  bg_dim_intro/game/breaks  Playfield.Background.Dim.*             §4.10
  bg_blur                   Playfield.Background.Blur (0-10 →
                            gaussian at load)                      §4.10
  bg_parallax               Playfield.Background.Parallax (bg
                            ~1.02×, offset opposite the cursor)    §4.10
  bg_triangles              the osu triangles pattern drifting up  §4.10
  load_storyboard           Playfield.Background.LoadStoryboards
                            — ACCEPTED + NO-OP (deferred subsystem;
                            danser-fallback path per APPROACH.md)  §4.10
  load_video                Playfield.Background.LoadVideos —
                            [Events] Video via render/video_bg.py  §4.10
  flash_to_beat             Background.FlashToTheBeat (bg
                            brightness pulses on red-line beats)   §4.10
  seizure_warning           Playfield.SeizureWarning — the dark
                            flashing-lights card, 5 s pre-roll     §4.10
  lead_in_time              Playfield.LeadInTime — extra pre-roll
                            hold before the map starts             §4.10
  fade_out_time             Playfield.FadeOutTime — video+audio
                            fade to black after the last object    §4.10
  skip_intro                -skip CLI (trims a long silent intro
                            to ~1 s before the first approach)     §4.15
  bloom / bloom_to_beat     Playfield.Bloom (post-process bright-
                            pass+blur+additive on the gameplay
                            layer; to-beat pulses the strength)    §4.10
  show_logo                 the intro logo splash — the R3D "R"
                            tile per house branding (not ppy's)    §4.5
  draw_cursor               Playfield.DrawCursors                  §4.10
  use_skin_cursor           Skin.Cursor.UseSkinCursor              §4.7
  cursor_scale              Cursor.CursorSize (÷ ref default)      §4.8
  cursor_trail_scale        Cursor.TrailScale (trail sprites only) §4.8
  cursor_long_trail         Skin.Cursor.ForceLongTrail             §4.7
  cursor_rainbow            Cursor.Colors.EnableRainbow            §4.8
  cursor_ripples            Cursor.CursorRipples (press-edge ring) §4.8
  draw_approach_circles     Objects.DrawApproachCircles            §4.9
  draw_combo_numbers        Objects.DrawComboNumbers               §4.9
  draw_follow_points        Objects.DrawFollowPoints               §4.9
  slider_snaking_in/out     Objects.Sliders.Snaking.In/Out         §4.9
  slider_merge              Objects.Sliders.SliderMerge — all
                            visible bodies in ONE distance pass    §4.9
  show_score/combo/hp_bar…  Gameplay.<Element>.Show                §4.6
  show_hit_error_meter      Gameplay.HitErrorMeter.Show            §4.6
  show_unstable_rate        HitErrorMeter.ShowUnstableRate         §4.6
  show_aim_error_meter      Gameplay.AimErrorMeter.Show (cursor-
                            offset-at-click scatter panel)         §4.6
  show_hit_counter          Gameplay.HitCounter.Show (live
                            300/100/50/miss column)                §4.6
  show_pp_counter           Gameplay.PPCounter.Show (rosu-pp
                            GradualPerformance; render/pp.py)      §4.6
  show_key_overlay          Gameplay.KeyOverlay.Show               §4.6
  show_scoreboard(+avatars) Gameplay.ScoreBoard.Show/ShowAvatars
                            — ACCEPTED + NO-OP (needs osu-API
                            leaderboard data; the future bot
                            integration hands the engine a JSON —
                            render/scoreboard.py is the loader
                            stub that will consume it)             §4.6
  show_mods                 Gameplay.Mods.Show (procedural pills)  §4.6
  show_boundaries           Gameplay.Boundaries.Enabled → mapped
                            to playfield_borders "full"/"none"     §4.6
  show_warning_arrows       Gameplay.ShowWarningArrows (break-end
                            flashing arrows)                       §4.6
  show_hit_lighting         Gameplay.ShowHitLighting               §4.6
  show_strain_graph         Gameplay.StrainGraph.Show (rosu-pp
                            strains, object-density proxy when
                            rosu is unavailable; render/pp.py)     §4.6
  results / results_screen_time  ShowResultsScreen/ResultsScreenTime §4.6
  watermark_text            (R3D-only; baked in-loop, not danser's
                            second ffmpeg pass)                    —
  letterbox_breaks          (in-house convention, catch/taiko)     —

Free-tier enforcement (720p30, forced watermark, storyboards/video/bloom/
triangles OFF, skip-intro ON, no results) lives in the SERVICE
(cli/r3d_render.py), not here — same as every other mode.

DEFAULTS POLICY: dataclass defaults keep the ENGINE's current CLI
behaviour (zero visual regression for direct invocations); where the R3D
preset default differs (nightcore OFF site-side, pp counter OFF,
seizure/logo ON site-side, lead_in 2 s, fade_out 5 s) the preset value
flows through from_preset(), which is the only path the service uses.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

# site encoder nicknames (presets._ENCODERS) → ffmpeg encoder names
_ENCODER_NICKNAMES = {
    "nvenc": "h264_nvenc",
    "vaapi": "h264_vaapi",
    "x264": "libx264",
}


@dataclass
class StdRenderSettings:
    # output (§4.13)
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 60
    encoder: str = "auto"
    encoder_device: str | None = None

    # skin (§4.7)
    skin_dir: Path | None = None
    default_skin_dir: Path | None = None
    skin_combo_colors: bool = True        # UseColorsFromSkin
    use_beatmap_colors: bool = False      # UseBeatmapColors ([Colours] wins)

    # audio (§4.4; volumes 0..100 like every in-house engine)
    music_volume: int = 100
    hitsound_volume: int = 100
    general_volume: int = 100
    audio_offset: int = 0                 # ms, the -offset CLI semantic
    # judged-hit hitsound mixing (hitsounds fire at the player's REAL
    # press times — what this engine always did; the preset key now
    # gates the whole hitsound track, the mania semantic)
    use_replay_hitsounds: bool = True
    # NC-mod beat overlay: clap each beat, finish each bar downbeat, from
    # the red timing points (record/hitsounds.nightcore_beats — the mania
    # _layer_nightcore mirror). Site default OFF (presets.py DEFAULTS).
    nightcore_hitsounds: bool = False
    use_skin_hitsounds: bool = False      # True = IgnoreBeatmapSamples

    # background (§4.10; dims 0..100, higher = darker; gameplay default is
    # HIGH like the R3D presets — readability over scenery)
    bg_dim_intro: int = 0
    bg_dim_game: int = 90
    bg_dim_breaks: int = 30
    bg_blur: int = 0                      # 0-10 → gaussian at texture load
    bg_parallax: bool = False             # bg ~1.02×, offset vs cursor
    bg_triangles: bool = False            # drifting osu-triangles deco
    load_storyboard: bool = False         # render/storyboard_render (phase 3); service gates free tier
    load_video: bool = False              # [Events] Video → video_bg.py
    flash_to_beat: bool = False           # bg brightness pulse on beats

    # effects (§4.10/§4.5)
    seizure_warning: bool = False         # 5 s warning card pre-roll
    bloom: bool = False                   # post-process bloom (GPU cost)
    bloom_to_beat: bool = True            # pulse bloom strength on beats
    show_logo: bool = False               # intro R3D "R" splash

    # flow (§4.10/§4.15)
    skip_intro: bool = True               # trim long silent intros (-skip)
    lead_in_time: float = 0.0             # extra pre-roll hold, wall SECONDS
    fade_out_time: float = 1.5            # fade to black, wall SECONDS
    show_results: bool = True
    results_screen_time: float = 5.0
    # outro style: "lazer" = the ported osu!(lazer) ranking screen
    # (render/lazer_results.py — the std default, two-stage: score panel
    # then the expanded statistics view); "r3d" = the in-house shared card
    # (render/results.py). show_results off skips the outro entirely in
    # either style. Grade colours stay lazer's under custom skins (the
    # results screen is client UI, skin-independent).
    results_style: str = "lazer"
    # per-map RENDER LEADERBOARD on the lazer results screen (owner mockup
    # 2026-07-09): the featured current-play card flanked by ranked cards of
    # OTHER renders of the same map, from the LOCAL render DB (no osu!API).
    # On by default for std; off → the current single-card results (+ the
    # existing PB card) are unchanged. Only affects results_style="lazer".
    show_leaderboard: bool = True
    letterbox_breaks: bool = True

    # cursor (§4.7/§4.8). use_skin_cursor defaults TRUE — real osu has no
    # such toggle: the skin's cursor is always used when the skin ships
    # one (per-element fallback keeps the procedural cursor otherwise);
    # --no-skin-cursor remains as an explicit override.
    draw_cursor: bool = True
    use_skin_cursor: bool = True
    cursor_scale: float = 1.0
    cursor_trail_scale: float = 1.0       # trail sprite size only
    cursor_long_trail: bool = False
    cursor_rainbow: bool = False          # hue-cycle cursor+trail tint
    cursor_ripples: bool = False          # expanding ring per press edge

    # objects (§4.9)
    draw_approach_circles: bool = True
    draw_combo_numbers: bool = True
    draw_follow_points: bool = True
    slider_snaking_in: bool = True
    slider_snaking_out: bool = True
    slider_merge: bool = False            # union-pass merged bodies
    stack_enabled: bool = True            # Objects.StackEnabled

    # HUD (§4.6) — render/hud.py: the osu!lazer HUD port (Argon components
    # when skinless, Legacy components + lg_* classic bakes under a custom
    # skin). show_hp_bar is LIVE (ruleset/health.py drain). pp counter =
    # render/pp.py rosu gradual (hides itself when rosu is unavailable).
    show_score: bool = True
    show_combo: bool = True
    show_hp_bar: bool = True
    show_grade: bool = True
    show_hit_error_meter: bool = True
    show_unstable_rate: bool = True
    show_aim_error_meter: bool = False    # site default: off
    show_hit_counter: bool = False
    show_pp_counter: bool = False         # site default: off (was ON while
    #                                       unimplemented — presets.py wins)
    show_strain_graph: bool = False       # site default: off
    show_key_overlay: bool = True
    show_mods: bool = True
    # ACCEPTED + NO-OP pair (docstring): stored, documented, not drawn.
    show_scoreboard: bool = True
    scoreboard_avatars: bool = False
    show_boundaries: bool = False         # preset bool; from_preset maps it
    #                                       onto playfield_borders
    show_warning_arrows: bool = True
    # §4.6 Gameplay.ShowHitLighting is false in the danser defaults, but the
    # R3D bot presets ship it ON — the preset field wins (same key name, so
    # from_preset() maps it straight through)
    show_hit_lighting: bool = True
    show_progress: bool = True            # §4.6 Score.ProgressBar
    progress_style: str = "pie"           # "pie" (plan default) | "bar"
    hud_scale: float = 1.0                # §4.6 shared Scale (global)
    hud_opacity: float = 1.0              # §4.6 shared Opacity (global)
    combo_break_flash: bool = True        # red edge-vignette pulse on breaks

    # owner punch-list additions (2026-07):
    # classic falling hit0 miss animation (falls + rotates while fading)
    miss_fall: bool = True
    # force the renderer's default (Argon) HUD numbers + procedural rank
    # text even when the skin ships fonts / ranking-* images
    renderer_default_font_and_ranks: bool = False
    # the ALL-LEGACY look (owner decision 2026-07-08): every HUD element
    # goes skin-else-legacy (the classic lg_* bakes — no Argon anywhere,
    # even skinless) and missing hitsound samples synthesize in the
    # LEGACY sound family instead of the Argon one. Gameplay elements
    # already fall back skin-else-legacy in every mode, so this only
    # moves the HUD component league + the synth hitsound bank.
    # PRECEDENCE: renderer_default_font_and_ranks WINS over
    # legacy_defaults when both are set — numbers/ranks go
    # Argon/procedural, everything else stays legacy.
    legacy_defaults: bool = False
    # subtle white playfield-bounds outline: "none" | "edges" | "full"
    playfield_borders: str = "none"

    # R3D service extras
    watermark_text: str = ""

    # preset-key aliases (the mania/catch preset names for fields whose
    # dataclass name differs)
    _PRESET_ALIASES = {
        "show_result_screen": "show_results",
        "results": "show_results",
    }

    @classmethod
    def from_preset(cls, preset: dict) -> "StdRenderSettings":
        """Build from an R3D preset dict (presets.validate() output or any
        subset) — THE service-adapter entry. Unknown keys (mania-only,
        catch-only, service-level) are ignored; value shapes are converted:

          resolution        "1920x1080" → (1920, 1080)
          encoder           "nvenc"/"vaapi"/"x264" → ffmpeg encoder names
          skin_combo_colors "skin"/"beatmap" → the two colour-source bools
          show_boundaries   bool → playfield_borders "full"/"none" (unless
                            an explicit playfield_borders key is present)
          lead_in_time/fade_out_time/results_screen_time  int → float
        """
        known = {f.name for f in fields(cls)}
        kwargs = {}
        for k, v in preset.items():
            k = cls._PRESET_ALIASES.get(k, k)
            if k == "skin_combo_colors" and isinstance(v, str):
                mode = v.strip().lower()
                kwargs["use_beatmap_colors"] = mode == "beatmap"
                kwargs["skin_combo_colors"] = mode != "beatmap"
                continue
            if k not in known:
                continue
            if k == "resolution" and isinstance(v, str):
                w, h = v.lower().split("x")
                v = (int(w), int(h))
            elif k == "encoder" and isinstance(v, str):
                v = _ENCODER_NICKNAMES.get(v.strip().lower(), v)
            elif k in ("lead_in_time", "fade_out_time",
                       "results_screen_time"):
                v = float(v)
            kwargs[k] = v
        # the site's Boundaries toggle drives the playfield borders unless
        # a caller passed the engine-native key explicitly
        if "show_boundaries" in kwargs and "playfield_borders" not in preset:
            kwargs["playfield_borders"] = ("full" if kwargs["show_boundaries"]
                                           else "none")
        return cls(**kwargs)
