"""Beatmap struct + timing-point wire parse — RENDER_PLAN.md §2.3 / §2.6
(app/beatmap/beatmap.go).

Field-for-field port of the §2.3 table, snake_cased. Runtime spawn logic
(Reset/Update) is included at scaffold level: spawn at startTime - Preempt,
finalize at endTime + HitFadeOut + Hit50 (beatmap.go:102).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .difficulty import HIT_FADE_OUT, Difficulty
from .objects.base import HitObject
from .objects.timing import Timings
from .pause import Pause
from .stacking import process_stacking


@dataclass
class Beatmap:
    # §2.3 metadata
    artist: str = ""
    artist_unicode: str = ""
    name: str = ""                  # Title
    name_unicode: str = ""
    difficulty_name: str = ""       # Version: in the file
    creator: str = ""
    source: str = ""
    tags: str = ""
    mode: int = 0
    slider_multiplier: float = 1.4
    stack_leniency: float = 0.7
    diff: Difficulty = field(default_factory=lambda: Difficulty(5, 5, 5, 5))
    dir: str = ""
    file: str = ""
    audio: str = ""
    bg: str = ""
    video: str = ""                 # [Events] Video,"file" (§4.10 LoadVideos)
    video_offset: int = 0           # Video start offset ms (may be negative)
    md5: str = ""
    set_id: int = -1
    id: int = -1
    last_modified: int = 0
    time_added: int = 0
    play_count: int = 0
    last_played: int = 0
    preview_time: int = 0
    stars: float = -1.0
    length: int = 0                 # last object time (ms)
    circles: int = 0
    sliders: int = 0
    spinners: int = 0
    min_bpm: float = math.inf
    max_bpm: float = 0.0
    timings: Timings = field(default_factory=Timings)
    hit_objects: list[HitObject] = field(default_factory=list)
    pauses: list[Pause] = field(default_factory=list)
    combo_colors: list[tuple[int, int, int]] = field(default_factory=list)  # [Colours]
    queue: list[HitObject] = field(default_factory=list)
    version: int = 14               # .osu format version
    ar_specified: bool = False
    local_offset: int = 0
    _processed: list[HitObject] = field(default_factory=list)
    _stack_calc_cache: dict[int, bool] = field(default_factory=dict)
    _path_cache: dict[str, Path] | None = None

    # --- §2.6 ParsePoint (beatmap.go:152) -------------------------------------
    # line = time,beatLength,meter,sampleSet,sampleIndex,volume,uninherited,effects
    def parse_point(self, line: str) -> None:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return
        try:
            time = float(parts[0])
            beat_length = float(parts[1])
        except ValueError:
            return
        if beat_length >= 0 and beat_length > 0:
            bpm = 60000.0 / beat_length
            self.min_bpm = min(self.min_bpm, bpm)
            self.max_bpm = max(self.max_bpm, bpm)
        signature = _int_or(parts[2], 4) if len(parts) > 2 else 4
        if signature == 0:
            signature = 4
        sample_set = _int_or(parts[3], self.timings.base_set) if len(parts) > 3 else self.timings.base_set
        sample_index = _int_or(parts[4], 1) if len(parts) > 4 else 1
        volume = (_int_or(parts[5], 100) / 100.0) if len(parts) > 5 else 1.0
        inherited = (len(parts) > 6 and _int_or(parts[6], 1) == 0)  # 0 = green line
        effects = _int_or(parts[7], 0) if len(parts) > 7 else 0
        kiai = bool(effects & 1)
        omit_first_bar_line = bool(effects & 8)
        self.timings.add_point(time, beat_length, sample_set, sample_index,
                               volume, signature, inherited, kiai,
                               omit_first_bar_line)

    def finalize_points(self) -> None:
        self.timings.finalize_points()

    # --- §2.3 CalculateStackLeniency (beatmap.go:234) --------------------------
    def calculate_stack_leniency(self, diff: Difficulty) -> None:
        """stackThreshold = floor(Preempt * StackLeniency); memoised so mods
        that change Preempt (EZ/HR via AR, DT via nothing — Preempt is
        rate-independent) recompute only once per threshold."""
        threshold = math.floor(diff.preempt * self.stack_leniency)
        if self._stack_calc_cache.get(threshold):
            return
        process_stacking(self.hit_objects, self.version, diff, self.stack_leniency)
        self._stack_calc_cache[threshold] = True

    # --- §2.3 Reset/Update (beatmap.go:87/:102) --------------------------------
    def reset(self) -> None:
        """Rebuild the spawn queue and re-derive per-object difficulty."""
        self.queue = sorted(self.hit_objects, key=lambda o: o.get_start_time())
        self._processed = []
        for obj in self.queue:
            obj.set_difficulty(self.diff)

    def update(self, time: float) -> tuple[list[HitObject], list[HitObject]]:
        """Spawn objects entering their preempt window; retire finished ones.

        beatmap.go:102 — spawn at startTime - Preempt, finalize at
        endTime + HitFadeOut + Hit50. Returns (spawned, retired).
        """
        spawned: list[HitObject] = []
        while self.queue and self.queue[0].get_start_time() - self.diff.preempt <= time:
            obj = self.queue.pop(0)
            self._processed.append(obj)
            spawned.append(obj)
        retired: list[HitObject] = []
        keep: list[HitObject] = []
        for obj in self._processed:
            if obj.get_end_time() + HIT_FADE_OUT + self.diff.hit50 <= time:
                retired.append(obj)
            else:
                keep.append(obj)
        self._processed = keep
        return spawned, retired

    @property
    def active_objects(self) -> list[HitObject]:
        return list(self._processed)

    # --- case-insensitive related-file lookup (§2.3 GetRelatedFile) ------------
    def get_related_file(self, base_dir: Path, name: str) -> Path | None:
        if not name:
            return None
        if self._path_cache is None:
            self._path_cache = {p.name.lower(): p
                                for p in Path(base_dir).rglob("*") if p.is_file()}
        return self._path_cache.get(name.lower())

    def get_audio_file(self, base_dir: Path) -> Path | None:
        return self.get_related_file(base_dir, self.audio)


def _int_or(s: str, default: int) -> int:
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default
