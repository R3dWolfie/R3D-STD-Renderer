"""Render settings — RENDER_PLAN.md §4 mapped onto R3D's preset keys.

The reference exposes a huge settings JSON (§4.2–§4.14). The service only
surfaces the subset in mania_ordr/presets.py (`_apply_preset_to_danser_cfg`
is the parity checklist, APPROACH.md §2.5); this dataclass is that subset,
named to match the R3D PRESET KEYS 1:1 so the std_renderer.py adapter can
map preset dict → CLI flags mechanically (same pattern as catch).

Mapping table (preset key ↔ reference §4 setting):

  R3D preset key            reference setting (§4)
  ------------------------  -------------------------------------------
  resolution / fps          Recording.FrameWidth/FrameHeight/FPS   §4.13
  encoder                   Recording.Encoder                      §4.13
  skin / use_replay_skin    Skin.CurrentSkin (resolver-extracted)  §4.7
  skin_combo_colors         Skin.UseColorsFromSkin/UseBeatmapColors §4.7/§3.5
  music_volume              Audio.MusicVolume                      §4.4
  hitsound_volume           Audio.SampleVolume                     §4.4
  general_volume            Audio.GeneralVolume                    §4.4
  audio_offset              -offset CLI (Audio.Offset is IGNORED
                            in recordings — §4.4 footgun)          §4.15
  nightcore_hitsounds       Audio.PlayNightcoreSamples             §4.4
  use_skin_hitsounds        Audio.IgnoreBeatmapSamples (inverted)  §4.4
  bg_dim_intro/game/breaks  Playfield.Background.Dim.*             §4.10
  bg_blur                   Playfield.Background.Blur              §4.10
  bg_parallax               Playfield.Background.Parallax          §4.10
  bg_triangles              Playfield.Background.Triangles         §4.10
  load_storyboard           Playfield.Background.LoadStoryboards   §4.10 (PUNTED — danser fallback)
  load_video                Playfield.Background.LoadVideos        §4.10 (video_bg.py exists)
  flash_to_beat             Background.FlashToTheBeat              §4.10
  seizure_warning           Playfield.SeizureWarning               §4.10
  lead_in_time              Playfield.LeadInTime (forced 0 in
                            record; we honor as pre-roll)          §4.10
  fade_out_time             Playfield.FadeOutTime                  §4.10
  skip_intro                -skip CLI                              §4.15
  bloom / bloom_to_beat     Playfield.Bloom                        §4.10 (later)
  draw_cursor               Playfield.DrawCursors                  §4.10
  use_skin_cursor           Skin.Cursor.UseSkinCursor              §4.7
  cursor_scale              Cursor.CursorSize (÷ ref default)      §4.8
  cursor_trail_scale        Cursor.TrailScale                      §4.8
  cursor_long_trail         Skin.Cursor.ForceLongTrail             §4.7
  cursor_rainbow            Cursor.Colors.EnableRainbow            §4.8
  cursor_ripples            Cursor.CursorRipples                   §4.8
  draw_approach_circles     Objects.DrawApproachCircles            §4.9
  draw_combo_numbers        Objects.DrawComboNumbers               §4.9
  draw_follow_points        Objects.DrawFollowPoints               §4.9
  slider_snaking_in/out     Objects.Sliders.Snaking.In/Out         §4.9
  slider_merge              Objects.Sliders.SliderMerge            §4.9 (later)
  show_score/combo/hp_bar…  Gameplay.<Element>.Show                §4.6
  show_hit_error_meter      Gameplay.HitErrorMeter.Show            §4.6
  show_unstable_rate        HitErrorMeter.ShowUnstableRate         §4.6
  show_aim_error_meter      Gameplay.AimErrorMeter.Show            §4.6 (later)
  show_hit_counter          Gameplay.HitCounter.Show               §4.6
  show_pp_counter           Gameplay.PPCounter.Show                §4.6
  show_key_overlay          Gameplay.KeyOverlay.Show               §4.6
  show_scoreboard(+avatars) Gameplay.ScoreBoard.Show/ShowAvatars   §4.6 (later — needs osu API)
  show_mods                 Gameplay.Mods.Show                     §4.6
  show_boundaries           Gameplay.Boundaries.Enabled            §4.6
  show_warning_arrows       Gameplay.ShowWarningArrows             §4.6
  show_hit_lighting         Gameplay.ShowHitLighting               §4.6
  show_strain_graph         Gameplay.StrainGraph.Show              §4.6 (later)
  results / results_screen_time  ShowResultsScreen/ResultsScreenTime §4.6
  watermark_text            (R3D-only; baked in-loop, not danser's
                            second ffmpeg pass)                    —
  letterbox_breaks          (in-house convention, catch/taiko)     —

Free-tier enforcement (720p30, forced watermark, storyboards/video/bloom/
triangles OFF, skip-intro ON, no results) lives in the SERVICE
(cli/r3d_render.py), not here — same as every other mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path


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
    use_beatmap_colors: bool = False      # UseBeatmapColors

    # audio (§4.4; volumes 0..100 like every in-house engine)
    music_volume: int = 100
    hitsound_volume: int = 100
    general_volume: int = 100
    audio_offset: int = 0                 # ms, the -offset CLI semantic
    nightcore_hitsounds: bool = True
    use_skin_hitsounds: bool = False      # True = IgnoreBeatmapSamples

    # background (§4.10; dims 0..100, higher = darker; gameplay default is
    # HIGH like the R3D presets — readability over scenery)
    bg_dim_intro: int = 0
    bg_dim_game: int = 90
    bg_dim_breaks: int = 30
    bg_blur: int = 0
    bg_parallax: bool = False
    load_video: bool = False
    flash_to_beat: bool = False
    seizure_warning: bool = False

    # flow (§4.10/§4.15)
    skip_intro: bool = True
    lead_in_time: float = 0.0             # forced 0 in record by the reference
    fade_out_time: float = 1.5
    show_results: bool = True
    results_screen_time: float = 5.0
    letterbox_breaks: bool = True

    # cursor (§4.7/§4.8)
    draw_cursor: bool = True
    use_skin_cursor: bool = False
    cursor_scale: float = 1.0
    cursor_trail_scale: float = 1.0
    cursor_long_trail: bool = False
    cursor_rainbow: bool = False
    cursor_ripples: bool = False

    # objects (§4.9)
    draw_approach_circles: bool = True
    draw_combo_numbers: bool = True
    draw_follow_points: bool = True
    slider_snaking_in: bool = True
    slider_snaking_out: bool = True
    stack_enabled: bool = True            # Objects.StackEnabled

    # HUD (§4.6) — the HUD phase implements score/acc/grade/progress/
    # combo/hit-error+UR/key-overlay/break-flash (render/hud.py). show_hp_bar,
    # show_pp_counter, show_hit_counter, show_mods and show_aim_error_meter
    # are accepted but their elements are later phases (hud.py docstring).
    show_score: bool = True
    show_combo: bool = True
    show_hp_bar: bool = True
    show_grade: bool = True
    show_hit_error_meter: bool = True
    show_unstable_rate: bool = True
    show_aim_error_meter: bool = False    # plan default: off
    show_hit_counter: bool = False
    show_pp_counter: bool = True
    show_key_overlay: bool = True
    show_mods: bool = True
    show_boundaries: bool = False
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

    # R3D service extras
    watermark_text: str = ""

    @classmethod
    def from_preset(cls, preset: dict) -> "StdRenderSettings":
        """Build from an R3D preset dict — unknown keys ignored, so the
        adapter can pass the full preset straight through."""
        known = {f.name for f in fields(cls)}
        kwargs = {}
        for k, v in preset.items():
            if k not in known:
                continue
            if k == "resolution" and isinstance(v, str):
                w, h = v.lower().split("x")
                v = (int(w), int(h))
            kwargs[k] = v
        return cls(**kwargs)
