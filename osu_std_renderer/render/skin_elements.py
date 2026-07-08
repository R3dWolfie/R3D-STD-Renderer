"""First real-skin support — the core gameplay textures of a user skin
(skin/skin.py resolution chain) uploaded for the scene to draw instead of
the procedural set. Per ELEMENT: found in the skin (or fallback skin) →
its texture is used; absent → the caller keeps the procedural texture,
which IS the §3.1 LOCAL source of this engine.

Elements loaded this phase (osu file names; animations take frame 0):

  hitcircle            tinted by combo colour (osu semantics)
  hitcircleoverlay     untinted; drawn above the circle, under the combo
                       number unless skin.ini HitCircleOverlayAboveNumber
  approachcircle       tinted by combo colour
  <HitCirclePrefix>-0..9  combo digits (skin.ini prefix, default
                       "default-"); ALL TEN or the set falls back —
                       mixed procedural/skin digit runs would look broken
  sliderb (sliderb0..N)   slider ball; frame 0 this phase. Tint: combo
                       colour if AllowSliderBallTint, else the skin.ini
                       SliderBall colour, else white. SliderBallFlip is
                       NOT honored yet (single frame → nothing to flip)
  sliderfollowcircle   drawn while tracking at 2.4× the circle diameter
  cursor / cursortrail / cursormiddle   CursorCentre honored (0 → the
                       texture hangs from the pointer, stable's anchor)
  hit0 / hit50 / hit100 / hit300   judgment sprites (`-0` animation frame
                       0). A skin's FULLY TRANSPARENT texture (classic
                       empty hit300.png) counts as LOADED but draws
                       NOTHING — real osu shows no 300 popup for it.
  reversearrow         repeat arrow on the slider end circle — WHITE
                       (never combo-tinted), beat-pulsed (scene draws it)
  sliderscorepoint     slider tick dot, untinted
  followpoint          same-combo connection dot (followpoint-0.. frames;
                       AnimationFramerate cycling via frame_key)
  sliderstartcircle(overlay) / sliderendcircle(overlay)
                       §3.3 slider head/tail specialisations, applied via
                       GetMostSpecific vs hitcircle(overlay) —
                       circle_elements() hands the scene the winning pair

ANIMATION FRAMES: sliderb and followpoint upload EVERY frame
(sk_<name> = frame 0, sk_<name>_fN beyond) and cycle on the map clock via
frame_key() (skin.ini GetFrameTime: AnimationFramerate>0 ? 1000/rate :
1000/frames). Everything else still takes frame 0.

SIZING — the osu convention: a 128 px @1x hitcircle spans the 64 osu!px
base radius, i.e. circle-tied textures draw at
`logical_px * (CircleRadius_screen_px / 64)` (circle_pixel_scale). @2x
files have logical size = pixel size / 2 (TextureFile.scale — resolution
logic in skin/skin.py). The cursor is UI-space, not playfield-space:
`logical_px * screen_h / 768` (stable's 768-line virtual UI).

NOT skinnable yet (honest list, stays procedural/absent): spinner (no
visuals at all), HUD (score/combo fonts, hit-error, key overlay — all
procedural), scorebar/hp (absent), hitcircle-full (mandala),
sliderb-nd/-spec companions, cursor rotate/expand animation, animation
frames beyond frame 0 for everything but sliderb/followpoint, hitsounds.
"""
from __future__ import annotations

from ..skin.skin import Skin

CIRCLE_TEX_RADIUS = 64.0     # 128px @1x hitcircle ↔ 64 osu!px base radius
CURSOR_UI_HEIGHT = 768.0     # stable's virtual UI height for cursor sizing
FOLLOW_CIRCLE_SCALE = 2.4    # follow circle diameter / circle diameter


def circle_pixel_scale(radius_px: float) -> float:
    """Screen px per LOGICAL skin px for circle-tied elements: a 128-px
    @1x hitcircle must span the circle's screen diameter 2·radius_px."""
    return radius_px / CIRCLE_TEX_RADIUS


def layout_skin_digits(number: int, sizes: dict[str, tuple[float, float]],
                       overlap: float) -> list[tuple[str, float, float, float]]:
    """[(char, dx_of_center, w, h)] for the digits of `number`, centred on
    dx=0, in LOGICAL skin px. `overlap` = skin.ini HitCircleOverlap
    (positive pulls digits together, negative spreads them — the default
    -2 is a 2 px gap)."""
    s = str(max(0, int(number)))
    widths = [sizes[ch][0] for ch in s]
    total = sum(widths) - overlap * (len(s) - 1)
    out: list[tuple[str, float, float, float]] = []
    x = -total / 2.0
    for ch, w in zip(s, widths):
        out.append((ch, x + w / 2.0, w, sizes[ch][1]))
        x += w - overlap
    return out


# element → (frames?, dash?, cycle?) — how the file resolves (§3.1
# GetFrames) and whether ALL frames upload for frame_key() cycling
_CORE_ELEMENTS: dict[str, tuple[bool, bool, bool]] = {
    "hitcircle": (False, False, False),
    "hitcircleoverlay": (False, False, False),
    "sliderstartcircle": (False, False, False),
    "sliderstartcircleoverlay": (False, False, False),
    "sliderendcircle": (False, False, False),
    "sliderendcircleoverlay": (False, False, False),
    "approachcircle": (False, False, False),
    "reversearrow": (False, False, False),
    "sliderscorepoint": (False, False, False),
    "followpoint": (True, True, True),  # followpoint-0.png.. animation
    "sliderb": (True, False, True),     # sliderb0.png.. animation, no dash
    "sliderfollowcircle": (True, False, False),
    "cursor": (False, False, False),
    "cursortrail": (False, False, False),
    "cursormiddle": (False, False, False),
    "hit0": (True, True, False),        # hit0-0.png.. animation, dashed
    "hit50": (True, True, False),
    "hit100": (True, True, False),
    "hit300": (True, True, False),
}


class SkinElements:
    """Loads the core set from a Skin into a SpriteRenderer under
    `sk_<element>` keys (digits: `sk_digit_<ch>`; animation frames beyond
    0: `sk_<element>_fN`).

    loaded       elements that resolved from the skin/fallback ("digits"
                 covers the whole HitCirclePrefix set)
    empty        loaded but fully transparent → draw NOTHING (hit300 case)
    size         element → (w, h) LOGICAL px (@2x already halved; frame 0)
    digit_sizes  digit char → (w, h) logical px
    frame_counts element → uploaded frame count (cycling elements only)
    """

    def __init__(self, skin: Skin, renderer):
        self.skin = skin
        self.info = skin.info
        self.loaded: set[str] = set()
        self.empty: set[str] = set()
        self.size: dict[str, tuple[float, float]] = {}
        self.digit_sizes: dict[str, tuple[float, float]] = {}
        self.frame_counts: dict[str, int] = {}

        for name, (frames, dash, cycle) in _CORE_ELEMENTS.items():
            self._load_one(renderer, name, frames=frames, dash=dash,
                           cycle=cycle)

        prefix = self.info.hit_circle_prefix or "default"
        tfs = {str(i): skin.find_texture(f"{prefix}-{i}") for i in range(10)}
        if all(tf is not None for tf in tfs.values()):
            for ch, tf in tfs.items():
                rgba = tf.load_rgba()
                renderer.upload_texture(f"sk_digit_{ch}", rgba)
                self.digit_sizes[ch] = (rgba.shape[1] * tf.scale,
                                        rgba.shape[0] * tf.scale)
            self.loaded.add("digits")

        # §3.3 slider head/tail specialisations (GetMostSpecific): the
        # (circle, overlay) element pair each role actually draws
        self._hit_pair = (self._pick("hitcircle"),
                          self._pick("hitcircleoverlay"))
        self._head_pair = (
            self._most_specific("sliderstartcircle", "hitcircle"),
            self._most_specific("sliderstartcircleoverlay",
                                "hitcircleoverlay"))
        self._end_pair = (
            self._most_specific("sliderendcircle", "hitcircle"),
            self._most_specific("sliderendcircleoverlay",
                                "hitcircleoverlay"))

    def _load_one(self, renderer, name: str, *, frames: bool,
                  dash: bool, cycle: bool = False) -> None:
        if frames:
            found = self.skin.find_frames(name, use_dash=dash)
            if not cycle:
                found = found[:1]          # frame 0 only
        else:
            tf = self.skin.find_texture(name)
            found = [tf] if tf is not None else []
        if not found:
            return
        n = 0
        for i, tf in enumerate(found):
            rgba = tf.load_rgba()
            if rgba.size == 0:
                if i == 0:
                    return
                break
            renderer.upload_texture(
                f"sk_{name}" if i == 0 else f"sk_{name}_f{i}", rgba)
            if i == 0:
                self.size[name] = (rgba.shape[1] * tf.scale,
                                   rgba.shape[0] * tf.scale)
                self.loaded.add(name)
                if int(rgba[..., 3].max()) == 0:
                    self.empty.add(name)   # skin explicitly blanks this
            n += 1
        if cycle and n:
            self.frame_counts[name] = n

    def has(self, name: str) -> bool:
        return name in self.loaded

    @staticmethod
    def key(name: str) -> str:
        return f"sk_{name}"

    def frame_key(self, name: str, t: float) -> str:
        """Texture key of `name` at map time t: cycling elements pick
        their frame per skin.ini GetFrameTime (AnimationFramerate > 0 ?
        1000/rate : 1000/frames); single-frame elements stay sk_<name>."""
        n = self.frame_counts.get(name, 1)
        if n <= 1:
            return f"sk_{name}"
        ft = self.info.get_frame_time(n)
        if ft <= 0:
            return f"sk_{name}"
        i = int(t / ft) % n
        return f"sk_{name}" if i == 0 else f"sk_{name}_f{i}"

    def _pick(self, name: str) -> str | None:
        return name if name in self.loaded else None

    def _most_specific(self, special: str, base: str) -> str | None:
        """§3.1 GetMostSpecific over LOADED elements: the specialised
        name wins unless the base resolved from a higher-priority source
        (skin.get_most_specific compares resolution sources)."""
        s_ok, b_ok = special in self.loaded, base in self.loaded
        if s_ok and b_ok:
            return self.skin.get_most_specific(special, base)
        if s_ok:
            return special
        if b_ok:
            return base
        return None

    def circle_elements(self, role: str = "hit") \
            -> tuple[str | None, str | None]:
        """(circle_element, overlay_element) a hit circle of `role`
        ("hit" | "slider_head" | "slider_end") draws; None → procedural."""
        if role == "slider_head":
            return self._head_pair
        if role == "slider_end":
            return self._end_pair
        return self._hit_pair

    def report_lines(self) -> list[str]:
        """The honesty lines: what came from the skin vs the procedural
        fallback (printed once per render)."""
        all_names = [*_CORE_ELEMENTS, "digits"]
        got = [n for n in all_names if n in self.loaded]
        missing = [n for n in all_names if n not in self.loaded]
        lines = [f"skin: elements from skin: {', '.join(got) or '(none)'}"]
        if missing:
            lines.append("skin: procedural fallback: " + ", ".join(missing))
        if self.empty:
            lines.append("skin: blanked by skin (drawn as nothing): "
                         + ", ".join(sorted(self.empty)))
        return lines
