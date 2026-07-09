"""Settings surface — from_preset() must consume the SITE's preset JSON
wholesale (mania_ordr/presets.py validate() output): every std key maps,
value shapes convert, mania/catch/service keys are ignored. The payload
below is presets.py's DEFAULTS verbatim (2026-07) with a few values
flipped so the mapping (not the defaults) is what's proven."""
from __future__ import annotations

from osu_std_renderer.settings import StdRenderSettings

# mania_ordr/mania_ordr/presets.py DEFAULTS — verbatim keys (the real
# payload shape the bot's adapter will pass), values flipped where noted
SITE_PRESET = {
    # osu!catch visuals (must be IGNORED here)
    "show_hyperdash": True, "fruit_rotation": True,
    "catcher_dash_trail": True, "banana_rainbow": True,
    "letterbox_breaks": True,
    # General
    "resolution": "1280x720",            # string form
    "fps": 30,
    "encoder": "nvenc",                  # site nickname → h264_nvenc
    "video_visibility": "public",        # service-level → ignored
    # Audio
    "music_volume": 80, "hitsound_volume": 70, "general_volume": 60,
    "use_replay_hitsounds": False,       # flipped
    "nightcore_hitsounds": True,         # flipped
    "audio_offset": -20,
    # Gameplay
    "scroll_speed": 17,                  # mania-only → ignored
    "show_key_overlay": False,           # flipped
    "show_combo": True, "show_score": True, "show_hp_bar": True,
    "show_hit_error_meter": True, "show_unstable_rate": True,
    "show_aim_error_meter": True,        # flipped
    "show_result_screen": False,         # flipped + ALIASED name
    "show_leaderboard": False,           # flipped (default on)
    "results_screen_time": 12,           # int → float
    "show_pp_counter": True,             # flipped
    "show_grade": True,
    "show_scoreboard": True, "scoreboard_avatars": False,
    "show_mods": True,
    "show_hit_counter": True,            # flipped
    "show_strain_graph": True,           # flipped
    "show_warning_arrows": True,
    "show_hit_lighting": True,
    "show_boundaries": True,             # bool → playfield_borders
    # Background
    "bg_dim_intro": 0, "bg_dim_game": 80, "bg_dim_breaks": 40,
    "bg_blur": 4, "bg_parallax": True, "load_storyboard": True,
    "load_video": True, "bg_triangles": True, "flash_to_beat": True,
    # Effects
    "seizure_warning": True, "bloom": True, "bloom_to_beat": True,
    "show_logo": True,
    # Cursor
    "draw_cursor": True, "use_skin_cursor": True,
    "cursor_scale": 1.4, "cursor_trail_scale": 0.7,
    "cursor_long_trail": True, "cursor_rainbow": True,
    "cursor_ripples": True,
    # Objects / Sliders
    "draw_approach_circles": False,      # flipped
    "draw_combo_numbers": True, "draw_follow_points": True,
    "slider_snaking_in": False,          # flipped
    "slider_snaking_out": False,         # flipped
    "slider_merge": True,                # flipped
    # Timing
    "skip_intro": False,                 # flipped
    "lead_in_time": 2,                   # int → float
    "fade_out_time": 5,                  # int → float
    # Skin
    "use_replay_skin": False,            # service-level → ignored
    "skin_combo_colors": "beatmap",      # string → the two bools
    "use_skin_hitsounds": True,          # flipped
    # Other
    "watermark_text": "renderer.r3dwolfie.com",
    "hide_judgement_line": False,        # mania-only → ignored
}


def test_from_preset_full_site_payload():
    s = StdRenderSettings.from_preset(SITE_PRESET)
    # shapes
    assert s.resolution == (1280, 720)
    assert s.fps == 30
    assert s.encoder == "h264_nvenc"                 # nickname mapped
    # audio
    assert s.music_volume == 80 and s.hitsound_volume == 70
    assert s.general_volume == 60 and s.audio_offset == -20
    assert s.use_replay_hitsounds is False
    assert s.nightcore_hitsounds is True
    assert s.use_skin_hitsounds is True
    # HUD
    assert s.show_key_overlay is False
    assert s.show_results is False                   # aliased key
    assert s.show_leaderboard is False               # flipped off
    assert s.results_screen_time == 12.0
    assert s.show_pp_counter and s.show_hit_counter
    assert s.show_aim_error_meter and s.show_strain_graph
    assert s.show_scoreboard is True and s.scoreboard_avatars is False
    # boundaries → borders
    assert s.show_boundaries is True
    assert s.playfield_borders == "full"
    # background/effects
    assert s.bg_dim_game == 80 and s.bg_blur == 4
    assert s.bg_parallax and s.bg_triangles and s.flash_to_beat
    assert s.load_storyboard and s.load_video
    assert s.seizure_warning and s.bloom and s.bloom_to_beat and s.show_logo
    # cursor
    assert s.cursor_scale == 1.4 and s.cursor_trail_scale == 0.7
    assert s.cursor_long_trail and s.cursor_rainbow and s.cursor_ripples
    # objects
    assert s.draw_approach_circles is False
    assert s.slider_snaking_in is False and s.slider_snaking_out is False
    assert s.slider_merge is True
    # timing (ints → floats, wall seconds)
    assert s.skip_intro is False
    assert s.lead_in_time == 2.0 and s.fade_out_time == 5.0
    # combo colour source string
    assert s.use_beatmap_colors is True
    assert s.skin_combo_colors is False
    # watermark
    assert s.watermark_text == "renderer.r3dwolfie.com"


def test_from_preset_skin_colors_and_boundaries_off():
    s = StdRenderSettings.from_preset(
        {"skin_combo_colors": "skin", "show_boundaries": False})
    assert s.skin_combo_colors is True and s.use_beatmap_colors is False
    assert s.playfield_borders == "none"


def test_from_preset_explicit_borders_key_wins():
    s = StdRenderSettings.from_preset(
        {"show_boundaries": True, "playfield_borders": "edges"})
    assert s.playfield_borders == "edges"


def test_from_preset_unknown_keys_ignored():
    s = StdRenderSettings.from_preset(
        {"scroll_speed": 25, "hide_judgement_line": True,
         "show_hyperdash": False, "use_replay_skin": True,
         "video_visibility": "unlisted", "some_future_key": 1})
    assert s == StdRenderSettings()          # nothing leaked through


def test_from_preset_encoder_passthrough_and_auto():
    assert StdRenderSettings.from_preset({"encoder": "auto"}).encoder == "auto"
    assert StdRenderSettings.from_preset(
        {"encoder": "vaapi"}).encoder == "h264_vaapi"
    assert StdRenderSettings.from_preset(
        {"encoder": "x264"}).encoder == "libx264"
    # already-full names pass through untouched
    assert StdRenderSettings.from_preset(
        {"encoder": "h264_nvenc"}).encoder == "h264_nvenc"


def test_engine_defaults_keep_current_cli_behaviour():
    """Zero-regression guard: new features default OFF for direct CLI
    use; site-differing defaults flow only through from_preset."""
    s = StdRenderSettings()
    assert s.nightcore_hitsounds is False
    assert s.show_pp_counter is False
    assert s.show_strain_graph is False and s.show_aim_error_meter is False
    assert s.bloom is False and s.show_logo is False
    assert s.seizure_warning is False
    assert s.bg_parallax is False and s.bg_triangles is False
    assert s.load_video is False and s.load_storyboard is False
    assert s.slider_merge is False
    assert s.lead_in_time == 0.0
    assert s.use_replay_hitsounds is True    # hitsounds stay ON (as before)
    assert s.slider_snaking_out is True      # flag existed; now rendered
    assert s.show_leaderboard is True        # new feature defaults ON for std
