"""§3.2 skin.ini quirks."""
from osu_std_renderer.skin import parse_skin_ini
from osu_std_renderer.skin.skin_ini import DEFAULT_COMBO_COLORS, SkinInfo

INI = """[General]
Name: Cool Skin
Author: Someone
// no Version key on purpose
AnimationFramerate: 24
CursorExpand: 0

[Colours]
Combo1: 255,0,0,255   // alpha must be ignored
Combo2: 0,128,255
SliderBorder: 10,20,30
SliderTrackOverride: 1,2,3

[Fonts]
HitCirclePrefix: mynum
HitCircleOverlap: 4
HitCircleOverlayAboveNumer: 1
"""


def test_full_parse():
    info = parse_skin_ini(INI)
    assert info.name == "Cool Skin"
    assert info.version == 1.0            # Version key ABSENT → 1.0
    assert info.legacy_v1_behavior
    assert info.animation_framerate == 24
    assert info.get_frame_time(10) == 1000 / 24
    assert not info.cursor_expand
    assert info.combo_colors == [(255, 0, 0), (0, 128, 255)]  # alpha ignored
    assert info.slider_border == (10, 20, 30)
    assert info.slider_track_override == (1, 2, 3)
    assert info.hit_circle_prefix == "mynum"
    assert info.hit_circle_overlap == 4
    assert info.hit_circle_overlay_above_number  # ...Numer spelling accepted


def test_version_latest_and_defaults():
    info = parse_skin_ini("Version: latest\n")
    assert info.version == 2.7
    blank = SkinInfo()                    # no skin.ini at all → 2.7 defaults
    assert blank.version == 2.7
    assert list(blank.combo_colors) == list(DEFAULT_COMBO_COLORS)
    assert blank.get_frame_time(5) == 200  # framerate -1 → 1000/frames
