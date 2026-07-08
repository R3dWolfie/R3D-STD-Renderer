"""Results screen (render/results.py — RED'S mania card ported to std):
mods formatting, histogram binning, rank-image source selection (the
item-5 toggle) and the stack draw. Pure CPU — the GL renderer is faked
(the card only needs upload_texture/draw/width/height)."""
from __future__ import annotations

from osu_std_renderer.render.results import (
    DIM_ALPHA, GRADE_BIG_ELEMENT, HIST_BINS, RESULTS_FADE_IN_MS,
    ResultsScreen, histogram_bins, mods_string,
)


class _FakeSpr:
    width, height = 1280, 720

    def __init__(self):
        self.textures = {}
        self.drawn = []

    def upload_texture(self, key, rgba):
        self.textures[key] = rgba.shape

    def draw(self, sprites):
        self.drawn.extend(sprites)


class _FakeSkin:
    def __init__(self, loaded, sizes=None):
        self.loaded = set(loaded)
        self.empty = set()
        self.size = sizes or {}

    def has(self, name):
        return name in self.loaded


def _card(spr=None, skin=None, **kw):
    args = dict(counts=(423, 63, 0, 23), acc_pct=87.23, score=423154,
                max_combo=214, grade="B", ur=142.3, avg_ms=-2.4,
                err_deltas=[-10.0, 5.0, 30.0, -80.0], meh_ms=120.0,
                player="vio", map_line="Artist - Title",
                diff_name="Extreme", mods=8, use_skin_ranks=True)
    args.update(kw)
    return ResultsScreen(spr or _FakeSpr(), skin, **args)


def test_mods_string():
    assert mods_string(0) == ""
    assert mods_string(8) == "HD"
    assert mods_string(8 | 64) == "HD,DT"
    assert mods_string(8 | 16) == "HD,HR"
    assert mods_string(512 | 64) == "NC"          # NC absorbs its DT bit
    assert mods_string(16384 | 32) == "PF"        # PF absorbs its SD bit
    assert mods_string(2 | 1 | 256) == "EZ,NF,HT"


def test_histogram_bins_shape_and_clipping():
    bins = histogram_bins([], HIST_BINS, 127.0)
    assert len(bins) == HIST_BINS and sum(bins) == 0
    # on-time hits land in the centre bin
    bins = histogram_bins([0.0, 0.1, -0.1], 25, 127.0)
    assert bins[12] == 3
    # out-of-range deltas clip to the edge bins
    bins = histogram_bins([-500.0, 500.0], 25, 127.0)
    assert bins[0] == 1 and bins[24] == 1
    assert sum(histogram_bins([12.0] * 7, 25, 127.0)) == 7


def test_grade_uses_skin_big_rank_image_when_present():
    """Item 5: the end-screen grade uses the skin's BIG ranking-* image
    when provided; the -small variants stay the HUD badge's."""
    assert GRADE_BIG_ELEMENT["SS"] == "ranking-X"
    skin = _FakeSkin({"ranking-B"}, {"ranking-B": (240.0, 240.0)})
    card = _card(skin=skin)
    assert card.grade_key == "sk_ranking-B"
    assert card.grade_size[1] > 0
    # no big image in the skin → procedural coloured letter
    card = _card(skin=_FakeSkin({"ranking-B-small"}))
    assert card.grade_key is None and card._grade_letter is not None


def test_renderer_default_ranks_toggle_forces_letter():
    skin = _FakeSkin({"ranking-B"}, {"ranking-B": (240.0, 240.0)})
    card = _card(skin=skin, use_skin_ranks=False)
    assert card.grade_key is None and card._grade_letter is not None


def test_draw_stack_dim_and_fade():
    spr = _FakeSpr()
    card = _card(spr=spr)
    card.set_windows(50.0, 100.0)
    card.draw(RESULTS_FADE_IN_MS / 2.0)          # half-faded
    assert spr.drawn, "card drew nothing"
    dim = spr.drawn[0]
    # the scene dim comes first, at DIM_ALPHA scaled by the fade
    assert dim.texture_key is None
    assert abs(dim.color[3] - DIM_ALPHA * 0.5) < 1e-9
    assert dim.w == spr.width and dim.h == spr.height
    # every sprite stays inside the frame vertically (the stack fits 720p)
    for s in spr.drawn:
        assert s.y - s.h / 2.0 >= -1e-6
        assert s.y + s.h / 2.0 <= spr.height + 1e-6
    # full fade → sprite alphas hit their full values
    spr2 = _FakeSpr()
    card2 = _card(spr=spr2)
    card2.set_windows(50.0, 100.0)
    card2.draw(RESULTS_FADE_IN_MS * 3)
    assert abs(spr2.drawn[0].color[3] - DIM_ALPHA) < 1e-9
    # before the start nothing draws
    spr3 = _FakeSpr()
    card3 = _card(spr=spr3)
    card3.draw(-1.0)
    assert spr3.drawn == []
