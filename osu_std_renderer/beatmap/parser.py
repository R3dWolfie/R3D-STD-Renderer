"""The 3-pass .osu parser — RENDER_PLAN.md §2.1–2.2 (app/beatmap/parser.go).

Pass 1  parse_beatmap()                   metadata + timing points + object
                                          COUNTS + Length (cheap, DB import)
Pass 2  parse_timing_points_and_pauses()  breaks + timing points only (when a
                                          cached map skipped them)
Pass 3  parse_objects()                   version line, [Colours], real
                                          IHitObjects, combo numbering,
                                          SetTiming (slider paths/ticks),
                                          stacking

Combo numbering (parser.go:362) ported exactly: spinners force a new combo
on themselves AND the next object; on new combo comboNumber=1, comboSet++,
comboSetHax += ColorOffset+1 (the colour-skip bits from the type byte);
the previous object gets LastInCombo.
"""
from __future__ import annotations

from pathlib import Path

from .beatmap import Beatmap
from .objects.base import (TYPE_CIRCLE, TYPE_LONGNOTE, TYPE_SLIDER,
                           TYPE_SPINNER, create_object)
from .objects.slider import Slider
from .objects.spinner import Spinner
from .pause import Pause


class BeatmapParseError(RuntimeError):
    pass


# --- helpers (parser.go:118-136) -----------------------------------------------

def tokenize(line: str, delimiter: str, n: int = -1) -> list[str] | None:
    """tokenizeN: None for `//` comments or missing delimiter; split + trim."""
    line = line.strip()
    if line.startswith("//") or delimiter not in line:
        return None
    parts = line.split(delimiter) if n < 0 else line.split(delimiter, n - 1)
    return [p.strip() for p in parts]


def get_section(line: str) -> str:
    return line.strip().strip("[]")


# --- section line handlers (parser.go:22-110) -----------------------------------

_SAMPLE_SETS = {"normal": 1, "all": 1, "soft": 2, "none": 2, "drum": 3}


def parse_general(line: str, beatmap: Beatmap) -> None:
    tok = tokenize(line, ":", 2)
    if not tok or len(tok) < 2:
        return
    key, value = tok[0], tok[1]
    if key == "Mode":
        beatmap.mode = _int(value, 0)
    elif key == "StackLeniency":
        v = _float(value, 0.7)
        beatmap.stack_leniency = 0.0 if v != v else v  # NaN → 0.0
    elif key == "AudioFilename":
        beatmap.audio = value
    elif key == "PreviewTime":
        beatmap.preview_time = _int(value, 0)
    elif key == "SampleSet":
        beatmap.timings.base_set = _SAMPLE_SETS.get(value.lower(), 1)


def parse_metadata(line: str, beatmap: Beatmap) -> None:
    tok = tokenize(line, ":", 2)
    if not tok or len(tok) < 2:
        return
    key, value = tok[0], tok[1]
    if key == "Title":
        beatmap.name = value
    elif key == "TitleUnicode":
        beatmap.name_unicode = value
    elif key == "Artist":
        beatmap.artist = value
    elif key == "ArtistUnicode":
        beatmap.artist_unicode = value
    elif key == "Creator":
        beatmap.creator = value
    elif key == "Version":
        beatmap.difficulty_name = value
    elif key == "Source":
        beatmap.source = value
    elif key == "Tags":
        beatmap.tags = value
    elif key == "BeatmapID":
        beatmap.id = _int(value, -1)
    elif key == "BeatmapSetID":
        beatmap.set_id = _int(value, -1)


def parse_difficulty(line: str, beatmap: Beatmap) -> None:
    """§2.2 parseDifficulty — AR/CS/OD/HP clamped [0,10]; if no AR was
    specified, AR := OD (pre-AR-era maps)."""
    tok = tokenize(line, ":", 2)
    if not tok or len(tok) < 2:
        return
    key, value = tok[0], tok[1]
    if key == "SliderMultiplier":
        v = _float(value, 1.4)
        beatmap.slider_multiplier = v
        beatmap.timings.slider_mult = v
    elif key == "ApproachRate":
        beatmap.diff.set_ar(_float(value, 5.0))
        beatmap.ar_specified = True
    elif key == "CircleSize":
        beatmap.diff.set_cs(_float(value, 5.0))
    elif key == "SliderTickRate":
        beatmap.timings.tick_rate = _float(value, 1.0)
    elif key == "HPDrainRate":
        beatmap.diff.set_hp(_float(value, 5.0))
    elif key == "OverallDifficulty":
        od = _float(value, 5.0)
        beatmap.diff.set_od(od)
        if not beatmap.ar_specified:
            beatmap.diff.set_ar(od)


def parse_events(line: str, beatmap: Beatmap) -> None:
    tok = tokenize(line, ",")
    if not tok:
        return
    if tok[0] in ("Background", "0") and len(tok) >= 3:
        beatmap.bg = tok[2].strip('"')
    elif tok[0] in ("Video", "1") and len(tok) >= 3:
        # Video,<startOffsetMs>,"file"[,xOffset,yOffset] (§4.10 LoadVideos;
        # negative offsets exist — the video starts before the audio)
        beatmap.video = tok[2].strip('"')
        beatmap.video_offset = _int(tok[1], 0)
    elif tok[0] in ("Break", "2") and len(tok) >= 3:
        p = Pause.parse(tok)
        if p is not None:
            beatmap.pauses.append(p)


# --- pass 1: ParseBeatMap (parser.go:152) ----------------------------------------

def parse_beatmap(path: Path, beatmap: Beatmap | None = None) -> Beatmap:
    """Import pass: metadata + timing points; [HitObjects] only COUNTED
    (LONGNOTE counts as slider) and Length = max(objectTime) — time index 2
    for circles/sliders, 5 for spinners, split(arr[5],":")[0] for holds."""
    path = Path(path)
    if beatmap is None:
        beatmap = Beatmap()
    beatmap.dir = path.parent.name
    beatmap.file = path.name

    section = ""
    for raw in _lines(path):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("["):
            section = get_section(line)
            continue
        if section in ("General",):
            parse_general(line, beatmap)
        elif section == "Metadata":
            parse_metadata(line, beatmap)
        elif section == "Difficulty":
            parse_difficulty(line, beatmap)
        elif section == "Events":
            parse_events(line, beatmap)
        elif section == "TimingPoints":
            beatmap.parse_point(line)
        elif section == "HitObjects":
            arr = line.split(",")
            if len(arr) < 5:
                continue
            obj_type = _int(arr[3], 0)
            time = 0
            if obj_type & TYPE_CIRCLE:
                beatmap.circles += 1
                time = _int(arr[2], 0)
            elif obj_type & (TYPE_SLIDER | TYPE_LONGNOTE):
                beatmap.sliders += 1
                if obj_type & TYPE_LONGNOTE and len(arr) > 5:
                    time = _int(arr[5].split(":")[0], 0)
                else:
                    time = _int(arr[2], 0)
            elif obj_type & TYPE_SPINNER:
                beatmap.spinners += 1
                time = _int(arr[5], 0) if len(arr) > 5 else _int(arr[2], 0)
            beatmap.length = max(beatmap.length, time)

    beatmap.finalize_points()
    if (beatmap.name + beatmap.artist + beatmap.creator) == "" \
            or not beatmap.timings.has_points():
        raise BeatmapParseError(f"corrupted file: {path}")
    return beatmap


def parse_beatmap_file(path: Path) -> Beatmap:
    """ParseBeatMapFile (:241)."""
    return parse_beatmap(Path(path))


# --- pass 2: ParseTimingPointsAndPauses (parser.go:258) --------------------------

def parse_timing_points_and_pauses(path: Path, beatmap: Beatmap) -> None:
    if beatmap.timings.has_points():
        return
    section = ""
    for raw in _lines(Path(path)):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("["):
            section = get_section(line)
            continue
        if section == "Events":
            tok = tokenize(line, ",")
            if tok and tok[0] in ("Break", "2"):
                p = Pause.parse(tok)
                if p is not None:
                    beatmap.pauses.append(p)
        elif section == "TimingPoints":
            beatmap.parse_point(line)
    beatmap.finalize_points()


# --- pass 3: ParseObjects (parser.go:303) ----------------------------------------

def parse_objects(path: Path, beatmap: Beatmap, *,
                  diff_calc_only: bool = False,
                  parse_colors: bool = True,
                  stack_enabled: bool = True) -> None:
    """Play/render pass: real hit objects, combos, slider paths, stacking."""
    path = Path(path)
    section = ""
    beatmap.combo_colors = []

    for raw in _lines(path):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("osu file format v"):
            beatmap.version = _int(line.rsplit("v", 1)[-1], 14)
            continue
        if line.startswith("["):
            section = get_section(line)
            continue
        if section == "Colours" and parse_colors:
            tok = tokenize(line, ":", 2)
            if tok and len(tok) >= 2 and tok[0].startswith("Combo"):
                rgb = [c.strip() for c in tok[1].split(",")]
                if len(rgb) >= 3:
                    try:
                        beatmap.combo_colors.append(
                            (int(rgb[0]), int(rgb[1]), int(rgb[2])))
                    except ValueError:
                        pass
        elif section == "HitObjects":
            arr = [t.strip() for t in line.split(",")]
            obj = create_object(arr)
            if obj is not None:
                beatmap.hit_objects.append(obj)

    # stable sort by start time (slices.SortStableFunc)
    beatmap.hit_objects.sort(key=lambda o: o.get_start_time())

    # --- combo numbering (parser.go:362) ---------------------------------------
    combo_number = 1
    combo_set = -1
    combo_set_hax = -1
    force_new_combo = False
    for i, obj in enumerate(beatmap.hit_objects):
        is_spinner = isinstance(obj, Spinner)
        new_combo = (i == 0 or obj.new_combo or is_spinner or force_new_combo)
        force_new_combo = is_spinner  # spinner forces new combo on the NEXT object
        if new_combo:
            obj.set_new_combo(True)
            combo_number = 1
            combo_set += 1
            combo_set_hax += obj.color_offset + 1  # colour-skip bits
            if i > 0:
                beatmap.hit_objects[i - 1].set_last_in_combo(True)
        else:
            combo_number += 1
        obj.hit_object_id = i
        obj.combo_number = combo_number
        obj.combo_set = combo_set
        obj.combo_set_hax = combo_set_hax
        obj.stack_leniency = beatmap.stack_leniency
    if beatmap.hit_objects:
        beatmap.hit_objects[-1].set_last_in_combo(True)

    # --- SetTiming: slider path/tick generation (parser.go step 6) --------------
    invalid: list = []
    for obj in beatmap.hit_objects:
        obj.set_timing(beatmap.timings, beatmap.version, diff_calc_only)
        if isinstance(obj, Slider) and obj.is_invalid():
            invalid.append(obj)
    # the reference degrades "retarded" sliders; scaffold drops them loudly
    for obj in invalid:
        beatmap.hit_objects.remove(obj)

    # --- stacking (parser.go step 7 → §2.7) --------------------------------------
    if stack_enabled or diff_calc_only:
        beatmap.calculate_stack_leniency(beatmap.diff)


# --- one-call convenience for tools/tests ----------------------------------------

def load_full(path: Path, mods: int = 0) -> Beatmap:
    """parse_beatmap + parse_objects with mods applied (the render entry)."""
    path = Path(path)
    beatmap = parse_beatmap_file(path)
    if mods:
        beatmap.diff.set_mods(mods)
    parse_objects(path, beatmap)
    return beatmap


def _lines(path: Path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        yield from fh


def _int(s: str, default: int) -> int:
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def _float(s: str, default: float) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return default
