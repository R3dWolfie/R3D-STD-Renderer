"""std skin.ini parser — RENDER_PLAN.md §3.2 (app/skin/info.go), full field
table. Parsing conventions adapted from the production mania v2 parser
(OsuManiaRenderer_v2/osu_mania_renderer_v2/skin_ini.py): key-based (section
headers ignored, like the reference), case-preserving keys matched
case-insensitively, `//` comments stripped, quirk-tolerant numbers.

Reference quirks ported:
  * latestVersion = 2.7; `Version` key ABSENT → 1.0; "latest" → 2.7;
    no skin.ini file at all → 2.7 (handled by load()).
  * `HitCircleOverlayAboveNumber` accepted under both spellings
    (…Number / …Numer).
  * ParseColor ignores alpha.
  * Combo bursts / slider style / MenuGlow / SpinnerBackground are
    deliberately NOT parsed (the reference skips them too).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

LATEST_VERSION = 2.7

# osu! default combo colours (§3.2 ComboColors default)
DEFAULT_COMBO_COLORS: tuple[tuple[int, int, int], ...] = (
    (255, 192, 0),
    (0, 202, 0),
    (18, 124, 255),
    (242, 24, 57),
)


@dataclass
class SkinInfo:
    """§3.2 SkinInfo — all fields, reference defaults."""
    name: str = ""
    author: str = ""
    version: float = LATEST_VERSION
    animation_framerate: float = -1.0
    spinner_fade_playfield: bool = True
    spinner_no_blink: bool = False
    spinner_frequency_modulate: bool = True
    layered_hit_sounds: bool = True
    cursor_centre: bool = True
    cursor_expand: bool = True
    cursor_rotate: bool = True
    combo_colors: list[tuple[int, int, int]] = field(
        default_factory=lambda: list(DEFAULT_COMBO_COLORS))
    # True only when the skin.ini itself defined Combo1..N — combo_colors
    # above always holds SOMETHING (the osu! defaults), so the combo-colour
    # precedence (cli.py §3.5/§4.7) needs this to know the skin actually
    # ships its own [Colours] palette (lazer: such a skin keeps its colours
    # even in "beatmap" mode).
    combo_colors_custom: bool = False
    default_skin_followpoint_behavior: bool = False
    slider_ball_tint: bool = False          # AllowSliderBallTint
    slider_ball_flip: bool = False
    slider_border: tuple[int, int, int] = (255, 255, 255)
    slider_track_override: tuple[int, int, int] | None = None
    slider_ball: tuple[int, int, int] | None = None
    song_select_inactive_text: tuple[int, int, int] = (255, 255, 255)
    song_select_active_text: tuple[int, int, int] = (0, 0, 0)
    input_overlay_text: tuple[int, int, int] = (0, 0, 0)
    hit_circle_prefix: str = "default"
    hit_circle_overlap: float = -2.0
    hit_circle_overlay_above_number: bool = False
    score_prefix: str = "score"
    score_overlap: float = 0.0
    combo_prefix: str = "score"
    combo_overlap: float = 0.0

    def get_frame_time(self, frames: int) -> float:
        """§3.2 GetFrameTime: AnimationFramerate>0 ? 1000/rate : 1000/frames."""
        if self.animation_framerate > 0:
            return 1000.0 / self.animation_framerate
        return 1000.0 / max(1, frames)

    # §3.2 version-gated behavior (circle.go): Version < 2 → reverse-arrow
    # pulse rotation ±6°, hit-explosion end scale 1.8 (vs 1.4), combo number
    # scales+fades with the explosion (vs quick 60 ms fade).
    @property
    def legacy_v1_behavior(self) -> bool:
        return self.version < 2


def parse_color(value: str) -> tuple[int, int, int] | None:
    """R,G,B[,A] — alpha IGNORED (reference ParseColor)."""
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < 3:
        return None
    try:
        r, g, b = (max(0, min(255, int(float(p)))) for p in parts[:3])
        return (r, g, b)
    except ValueError:
        return None


def _bool(value: str, default: bool) -> bool:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _float(value: str, default: float) -> float:
    try:
        return float(value.strip())
    except ValueError:
        return default


def parse_skin_ini(text: str) -> SkinInfo:
    """Parse skin.ini TEXT. Key-based like the reference — section headers
    are ignored entirely; keys win wherever they appear."""
    info = SkinInfo()
    version_seen = False
    combo_seen: dict[int, tuple[int, int, int]] = {}

    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line or line.startswith("[") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue

        if key == "name":
            info.name = value
        elif key == "author":
            info.author = value
        elif key == "version":
            version_seen = True
            info.version = LATEST_VERSION if value.lower() == "latest" \
                else _float(value, 1.0)
        elif key == "animationframerate":
            info.animation_framerate = _float(value, -1.0)
        elif key == "spinnerfadeplayfield":
            info.spinner_fade_playfield = _bool(value, True)
        elif key == "spinnernoblink":
            info.spinner_no_blink = _bool(value, False)
        elif key == "spinnerfrequencymodulate":
            info.spinner_frequency_modulate = _bool(value, True)
        elif key == "layeredhitsounds":
            info.layered_hit_sounds = _bool(value, True)
        elif key == "cursorcentre":
            info.cursor_centre = _bool(value, True)
        elif key == "cursorexpand":
            info.cursor_expand = _bool(value, True)
        elif key == "cursorrotate":
            info.cursor_rotate = _bool(value, True)
        elif key.startswith("combo") and key[5:].isdigit():
            c = parse_color(value)
            if c is not None:
                combo_seen[int(key[5:])] = c
        elif key == "defaultskinfollowpointbehavior":
            info.default_skin_followpoint_behavior = _bool(value, False)
        elif key == "allowsliderballtint":
            info.slider_ball_tint = _bool(value, False)
        elif key == "sliderballflip":
            info.slider_ball_flip = _bool(value, False)
        elif key == "sliderborder":
            info.slider_border = parse_color(value) or info.slider_border
        elif key == "slidertrackoverride":
            info.slider_track_override = parse_color(value)
        elif key == "sliderball":
            info.slider_ball = parse_color(value)
        elif key == "songselectinactivetext":
            info.song_select_inactive_text = parse_color(value) or info.song_select_inactive_text
        elif key == "songselectactivetext":
            info.song_select_active_text = parse_color(value) or info.song_select_active_text
        elif key == "inputoverlaytext":
            info.input_overlay_text = parse_color(value) or info.input_overlay_text
        elif key == "hitcircleprefix":
            info.hit_circle_prefix = value
        elif key == "hitcircleoverlap":
            info.hit_circle_overlap = _float(value, -2.0)
        elif key in ("hitcircleoverlayabovenumber", "hitcircleoverlayabovenumer"):
            info.hit_circle_overlay_above_number = _bool(value, False)
        elif key == "scoreprefix":
            info.score_prefix = value
        elif key == "scoreoverlap":
            info.score_overlap = _float(value, 0.0)
        elif key == "comboprefix":
            info.combo_prefix = value
        elif key == "combooverlap":
            info.combo_overlap = _float(value, 0.0)
        # anything unrecognised is ignored (reference behavior)

    if not version_seen:
        info.version = 1.0  # §3.2: Version key ABSENT → 1.0
    if combo_seen:
        info.combo_colors = [combo_seen[i] for i in sorted(combo_seen)]
        info.combo_colors_custom = True
    return info


def load(skin_dir: Path | None) -> SkinInfo:
    """Load `<skin_dir>/skin.ini`. No skin.ini at all → defaults with
    version = latest (2.7) — newDefaultInfo(). Corrupt file → defaults
    (caller may fall back to another skin per §3.1)."""
    if skin_dir is None:
        return SkinInfo()
    ini = _find_ini(Path(skin_dir))
    if ini is None:
        return SkinInfo()  # version stays LATEST — §3.2 "no skin.ini → 2.7"
    try:
        return parse_skin_ini(ini.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return SkinInfo()


def _find_ini(skin_dir: Path) -> Path | None:
    """Case-insensitive skin.ini lookup (skins zipped on Windows vary)."""
    direct = skin_dir / "skin.ini"
    if direct.exists():
        return direct
    try:
        for p in skin_dir.iterdir():
            if p.is_file() and p.name.lower() == "skin.ini":
                return p
    except OSError:
        pass
    return None
