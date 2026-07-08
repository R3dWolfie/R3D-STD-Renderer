"""Hit-object base — RENDER_PLAN.md §2.5 (objects/util.go, baseobject.go,
hitobject.go).

commonParse wire format (comma-split .osu [HitObjects] line):
    data[0],data[1] → StartPosRaw/EndPosRaw
    data[2]         → StartTime = EndTime
    data[3]         → objType; NewCombo=(objType&4)!=0; ColorOffset=(objType>>4)&7
    data[extraIndex]→ BasicHitSound "sampleSet:additionSet:customIndex:volume"

ModifyPosition (hitobject.go:207) applies HR/Mirror flips + the stacking
pixel offset; std stores stack indices per threshold in StackIndexMap.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..difficulty import Difficulty, Mods

# §2.5 type flags (util.go:27)
TYPE_CIRCLE = 1
TYPE_SLIDER = 2
TYPE_NEWCOMBO = 4
TYPE_SPINNER = 8
TYPE_LONGNOTE = 128  # mania hold; counted as "slider" in pass-1 counts


@dataclass
class HitSound:
    """parseExtras: `sampleSet:additionSet:customIndex:volume[:filename]`."""
    sample_set: int = 0
    addition_set: int = 0
    custom_index: int = 0
    volume: float = 0.0

    @classmethod
    def parse(cls, token: str | None) -> "HitSound":
        hs = cls()
        if not token:
            return hs
        parts = token.split(":")
        try:
            if len(parts) > 0 and parts[0]:
                hs.sample_set = int(parts[0])
            if len(parts) > 1 and parts[1]:
                hs.addition_set = int(parts[1])
            if len(parts) > 2 and parts[2]:
                hs.custom_index = int(parts[2])
            if len(parts) > 3 and parts[3]:
                hs.volume = float(parts[3]) / 100.0
        except ValueError:
            pass
        return hs


@dataclass
class HitObject:
    """§2.5 HitObject base (hitobject.go:64). Subclasses: Circle, Slider,
    Spinner. Positions are RAW map coordinates (no flips/stacking) — flips
    and stack offsets are applied per-query by modify_position(), exactly
    like the reference (raw geometry is shared; presentation is per-mod)."""
    start_pos_raw: tuple[float, float] = (0.0, 0.0)
    end_pos_raw: tuple[float, float] = (0.0, 0.0)
    start_time: float = 0.0
    end_time: float = 0.0
    stack_leniency: float = 0.7
    stack_index_map: dict[int, int] = field(default_factory=dict)
    hit_object_id: int = 0
    last_in_combo: bool = False
    new_combo: bool = False
    combo_number: int = 1
    combo_set: int = 0
    combo_set_hax: int = 0
    color_offset: int = 0
    basic_hit_sound: HitSound = field(default_factory=HitSound)
    hit_sound_bits: int = 0   # data[4] bitmask (normal/whistle/finish/clap)

    def common_parse(self, data: list[str], extra_index: int) -> None:
        """§2.5 commonParse(data, extraIndex)."""
        x, y = float(data[0]), float(data[1])
        self.start_pos_raw = (x, y)
        self.end_pos_raw = (x, y)
        self.start_time = float(data[2])
        self.end_time = self.start_time
        obj_type = int(data[3])
        self.new_combo = (obj_type & TYPE_NEWCOMBO) != 0
        self.color_offset = (obj_type >> 4) & 7
        self.hit_sound_bits = int(data[4]) if len(data) > 4 else 0
        token = data[extra_index] if len(data) > extra_index else None
        self.basic_hit_sound = HitSound.parse(token)

    # --- timing/difficulty hooks (overridden by Slider) ----------------------
    def set_timing(self, timings, version: int, diff_calc_only: bool = False) -> None:
        """§2.2 step 6: slider path/tick generation happens here (no-op for
        circles/spinners beyond storing the container)."""
        self.timings = timings

    def set_difficulty(self, diff: Difficulty) -> None:
        """Runtime sprite/fade construction (§2.2 'Runtime'). Scaffold: the
        draw path is not built yet; renderers derive fades from
        diff.preempt/time_fade_in on the fly."""
        self.diff = diff

    # --- positions ------------------------------------------------------------
    def get_start_time(self) -> float:
        return self.start_time

    def get_end_time(self) -> float:
        return self.end_time

    def get_stack_index(self, diff: Difficulty) -> int:
        threshold = stack_threshold(diff, self.stack_leniency)
        return self.stack_index_map.get(threshold, 0)

    def modify_position(self, base_pos: tuple[float, float],
                        diff: Difficulty) -> tuple[float, float]:
        """§2.5 ModifyPosition (hitobject.go:207):

            hFlip: X = 512-X   (mirror-horizontal; unused by default)
            vFlip: Y = 384-Y   (HardRock XOR mirror-vertical)
            stackOffset(stable) = stackIndex * CircleRadius / 10
            pos -= (stackOffset, stackOffset)
        """
        x, y = base_pos
        if diff.mods & Mods.HARD_ROCK:
            y = 384.0 - y
        offset = self.get_stack_index(diff) * diff.circle_radius / 10.0
        return (x - offset, y - offset)

    def get_stacked_start_position(self, diff: Difficulty) -> tuple[float, float]:
        return self.modify_position(self.start_pos_raw, diff)

    def get_stacked_end_position(self, diff: Difficulty) -> tuple[float, float]:
        return self.modify_position(self.end_pos_raw, diff)

    # combo-numbering setters (parser.go:362 calls these) ---------------------
    def set_new_combo(self, v: bool) -> None:
        self.new_combo = v

    def set_last_in_combo(self, v: bool) -> None:
        self.last_in_combo = v


def stack_threshold(diff: Difficulty, stack_leniency: float) -> int:
    """§2.7: stackThreshold = floor(Preempt * StackLeniency)."""
    return math.floor(diff.preempt * stack_leniency)


def create_object(data: list[str]):
    """§2.5 CreateObject(line): CIRCLE→Circle, SPINNER→Spinner, SLIDER→Slider
    (None if the slider is invalid). Mania LONGNOTE lines never reach a std
    renderer. Import is deferred to avoid a base↔slider cycle."""
    from .circle import Circle
    from .slider import Slider
    from .spinner import Spinner

    if len(data) < 5:
        return None
    try:
        obj_type = int(data[3])
    except ValueError:
        return None
    if obj_type & TYPE_CIRCLE:
        return Circle.parse(data)
    if obj_type & TYPE_SPINNER:
        return Spinner.parse(data)
    if obj_type & TYPE_SLIDER:
        return Slider.parse(data)  # may be None (invalid curve)
    return None
