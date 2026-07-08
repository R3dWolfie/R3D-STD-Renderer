"""Skin source resolution — RENDER_PLAN.md §3.1 / §3.3 (app/skin/skin.go).

STRUCTURE COMPLETE / LOADER PARTIAL: file resolution (fallback chain, @2x,
animation frames, sample extension chain) is implemented; the GPU atlas and
the procedural-Argon default set are later phases (taiko argon/ playbook).

§3.1 semantics ported:
  * Source priority (high→low): SKIN (current skin folder) → FALLBACK
    (fallback skin folder) → LOCAL (our bundled default assets).
  * BEATMAP is in the enum but STRIPPED from every texture lookup
    (`source &= ^BEATMAP`) — beatmap-folder images are never skin
    textures. Beatmap contribution is hitsounds + combo colours only
    (§3.6); hitsound file resolution honors that at a later phase.
  * @2x: `<base>@2x.<ext>` preferred; logical size = pixel size / 2.
  * GetFrames: if `<name><dash>0` exists and beats the single texture in
    specificity, collect `<name><dash>N` frames RESTRICTED TO THE SAME
    SOURCE (no cross-skin frame mixing); else single frame; else empty.
  * Companion textures lock to their parent's source (cursormiddle→cursor,
    sliderb-nd/-spec→sliderb, particleN→hitN) via get_source().
  * Case-insensitive filename lookup (FileMap).

§3.3 asset surface (what the std draw phase will request) is documented in
ELEMENT_ASSETS below so the atlas phase has a checklist.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .skin_ini import SkinInfo, load as load_skin_ini


class Source(enum.IntFlag):
    """§3.1 Source enum."""
    UNKNOWN = 0
    LOCAL = 2
    FALLBACK = 4
    SKIN = 8
    BEATMAP = 16
    ALL = LOCAL | FALLBACK | SKIN | BEATMAP


_TEX_EXTS = (".png", ".jpg", ".jpeg")
_SAMPLE_EXTS = (".wav", ".ogg", ".mp3")   # §3.1 GetSample extension order

# §3.3: element → skin assets (the atlas-phase checklist).
ELEMENT_ASSETS: dict[str, tuple[str, ...]] = {
    "hitcircle": ("hitcircle", "hitcircleoverlay", "sliderstartcircle",
                  "sliderstartcircleoverlay", "sliderendcircle",
                  "sliderendcircleoverlay", "hitcircle-full",
                  "approachcircle", "reversearrow"),
    "slider": ("sliderb", "sliderb-nd", "sliderb-spec", "sliderfollowcircle",
               "sliderscorepoint"),  # body is GENERATED (render/slider_body.py)
    "spinner-old": ("spinner-background", "spinner-metre", "spinner-circle"),
    "spinner-new": ("spinner-glow", "spinner-bottom", "spinner-top",
                    "spinner-middle2", "spinner-middle"),
    "spinner-common": ("spinner-approachcircle", "spinner-clear",
                       "spinner-spin", "spinner-rpm"),
    "hitresults": ("hit0", "hit50", "hit100", "hit100k", "hit300", "hit300k",
                   "hit300g", "particle50", "particle100", "particle300",
                   "lighting"),
    "hud": ("scorebar-bg", "scorebar-colour", "scorebar-marker", "scorebar-ki",
            "scorebar-kidanger", "scorebar-kidanger2", "section-pass",
            "section-fail", "inputoverlay-background", "inputoverlay-key",
            "play-skip", "arrow-warning"),
    "cursor": ("cursor", "cursortrail", "cursormiddle", "cursor-ripple",
               "cursor-smoke"),
    "followpoint": ("followpoint",),
}


@dataclass
class TextureFile:
    """A resolved skin texture on disk (pre-atlas)."""
    path: Path
    source: Source
    two_x: bool          # @2x variant → logical size = pixel size / 2

    def load_rgba(self) -> np.ndarray:
        img = Image.open(self.path).convert("RGBA")
        return np.asarray(img, dtype=np.uint8)

    @property
    def scale(self) -> float:
        return 0.5 if self.two_x else 1.0


class Skin:
    """§3.1 lookup chain over extracted skin folders.

    skin_dir     — the user's extracted .osk (SKIN source)
    fallback_dir — optional fallback skin (FALLBACK source)
    local_dir    — our bundled default assets (LOCAL source; procedural
                   Argon default lands here in the skin-parity phase)
    """

    def __init__(self, skin_dir: Path | None = None,
                 fallback_dir: Path | None = None,
                 local_dir: Path | None = None):
        self.skin_dir = Path(skin_dir) if skin_dir else None
        self.fallback_dir = Path(fallback_dir) if fallback_dir else None
        self.local_dir = Path(local_dir) if local_dir else None
        self.info: SkinInfo = load_skin_ini(self.skin_dir)
        # case-insensitive FileMaps per source
        self._maps: dict[Source, dict[str, Path]] = {}
        for src, d in ((Source.SKIN, self.skin_dir),
                       (Source.FALLBACK, self.fallback_dir),
                       (Source.LOCAL, self.local_dir)):
            self._maps[src] = _file_map(d)
        self._source_cache: dict[str, Source] = {}

    # --- §3.1 GetTextureSource -------------------------------------------------
    def find_texture(self, name: str,
                     source: Source = Source.ALL) -> TextureFile | None:
        source &= ~Source.BEATMAP  # BEATMAP stripped from every texture lookup
        for src in (Source.SKIN, Source.FALLBACK, Source.LOCAL):
            if not (source & src):
                continue
            tf = self._find_in(src, name)
            if tf is not None:
                self._source_cache[name.lower()] = src
                return tf
        return None

    def _find_in(self, src: Source, name: str) -> TextureFile | None:
        fmap = self._maps.get(src) or {}
        for ext in _TEX_EXTS:
            two_x = fmap.get(f"{name}@2x{ext}".lower())
            if two_x is not None:
                return TextureFile(two_x, src, True)
        for ext in _TEX_EXTS:
            one_x = fmap.get(f"{name}{ext}".lower())
            if one_x is not None:
                return TextureFile(one_x, src, False)
        return None

    def get_source(self, name: str) -> Source:
        """Source that resolved `name` (for companion-texture locking)."""
        cached = self._source_cache.get(name.lower())
        if cached is not None:
            return cached
        tf = self.find_texture(name)
        return tf.source if tf else Source.UNKNOWN

    def get_most_specific(self, name1: str, name2: str) -> str:
        """§3.1 GetMostSpecific: higher-priority source wins (e.g. whether
        sliderstartcircle overrides hitcircle)."""
        s1, s2 = self.get_source(name1), self.get_source(name2)
        return name1 if s1 >= s2 else name2

    # --- §3.1 GetFrames ----------------------------------------------------------
    def find_frames(self, name: str, use_dash: bool = False) -> list[TextureFile]:
        """Animation frames `<name><dash>0..N`, single-source; falls back to
        the single texture; [] when nothing exists."""
        dash = "-" if use_dash else ""
        single = self.find_texture(name)
        first = self.find_texture(f"{name}{dash}0")
        if first is not None and (single is None or first.source >= single.source):
            frames = [first]
            src = first.source  # frames restricted to the SAME source
            i = 1
            while True:
                nxt = self._find_in(src, f"{name}{dash}{i}")
                if nxt is None:
                    break
                frames.append(nxt)
                i += 1
            return frames
        return [single] if single is not None else []

    # --- §3.1 GetSample ------------------------------------------------------------
    def find_sample(self, name: str) -> Path | None:
        for src in (Source.SKIN, Source.FALLBACK, Source.LOCAL):
            fmap = self._maps.get(src) or {}
            for ext in _SAMPLE_EXTS:
                p = fmap.get(f"{name}{ext}".lower())
                if p is not None:
                    return p
        return None

    # --- §3.5 combo colour resolution ----------------------------------------------
    def get_combo_color(self, combo_set: int, combo_set_hax: int,
                        beatmap_colors: list[tuple[int, int, int]] | None,
                        *, use_skin_colors: bool = True,
                        use_beatmap_colors: bool = False) -> tuple[int, int, int]:
        """§3.5 skin.GetColor, reduced to the renderer-relevant branches:

            UseColorsFromSkin && colors exist:
                colors = UseBeatmapColors && map has colours ? beatmap : skin
                pick colors[(UseBeatmapColors ? comboSetHax : comboSet) % len]
        """
        if use_beatmap_colors and beatmap_colors:
            return beatmap_colors[combo_set_hax % len(beatmap_colors)]
        if use_skin_colors and self.info.combo_colors:
            return self.info.combo_colors[combo_set % len(self.info.combo_colors)]
        return (0, 255, 255)  # settings-HSV path lands with settings wiring


def _file_map(directory: Path | None) -> dict[str, Path]:
    """Case-insensitive filename → path (reference FileMap)."""
    if directory is None:
        return {}
    d = Path(directory)
    if not d.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out.setdefault(p.name.lower(), p)
    return out
