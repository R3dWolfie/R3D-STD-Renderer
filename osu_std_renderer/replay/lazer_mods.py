"""Read the lazer mod-acronym list from a .osr's appended ScoreInfo blob.

osu!lazer's LegacyScoreEncoder writes the standard .osr fields plus a tail
extension: after the 8-byte online/score id, a 4-byte little-endian length
prefix followed by an LZMA1 (alone-format) compressed JSON blob carrying the
full lazer ScoreInfo — including the ``mods`` list, where each entry is a
``{"acronym": "..."}`` dict (optionally with a ``settings`` object). The
legacy 32-bit ``mods`` bitmask osrparse exposes CANNOT represent the Classic
mod (acronym ``"CL"``), so the ScoreInfo blob is the only reliable source.

The blob-locating walk is ported from the production bot's
``mania_ordr/lazer_extension.py`` (``parse_lazer_score_info`` — the technique
the live versus/telemetry pipeline uses to read the ScoreInfo blob), reused
here so the std renderer can auto-detect the Classic mod from a .osr without
depending on the bot package.
"""
from __future__ import annotations

import json
import logging
import lzma
import struct
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# .osr game_version cutoff: stable writes 8-digit YYYYMMDD dates (~20251128),
# lazer writes 9-digit identifiers (~30000016). At/above this is a lazer
# replay — the only kind that carries the ScoreInfo blob / a Classic mod.
LAZER_GAME_VERSION = 30_000_000


def _read_uleb128(buf: bytes, pos: int) -> tuple[int, int]:
    """Read a ULEB128 unsigned int at ``pos`` → (value, new_position)."""
    val = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("uleb128 truncated")
        byte = buf[pos]
        pos += 1
        val |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return val, pos
        shift += 7
        if shift > 63:
            raise ValueError("uleb128 too long")


def _skip_string(buf: bytes, pos: int) -> int:
    """Skip an osu! .osr string in place → new position.
    0x00 = null/empty; 0x0b = ULEB128 length then that many UTF-8 bytes."""
    if pos >= len(buf):
        raise ValueError("string marker EOF")
    marker = buf[pos]
    pos += 1
    if marker == 0x00:
        return pos
    if marker != 0x0B:
        raise ValueError(f"unknown string marker {marker:#x}")
    length, pos = _read_uleb128(buf, pos)
    return pos + length


def parse_lazer_score_info(osr_path: Path) -> dict | None:
    """The lazer ScoreInfo JSON as a dict, or None when the .osr carries no
    (parseable) blob — a genuine stable .osr, a truncated/corrupt tail, or a
    future lazer schema this walk can't locate. Never raises."""
    try:
        buf = Path(osr_path).read_bytes()
    except OSError:
        return None
    if len(buf) < 32:
        return None
    pos = 0
    try:
        pos += 1                       # mode (byte)
        pos += 4                       # game_version (int)
        pos = _skip_string(buf, pos)   # beatmap_hash
        pos = _skip_string(buf, pos)   # username
        pos = _skip_string(buf, pos)   # replay_hash
        pos += 6 * 2                   # 6 judgement counts (shorts)
        pos += 4                       # total score (int)
        pos += 2                       # max_combo (short)
        pos += 1                       # perfect (byte)
        pos += 4                       # legacy mods bitfield (int)
        pos = _skip_string(buf, pos)   # life_bar_graph
        pos += 8                       # timestamp (long)
        if pos + 4 > len(buf):
            return None
        (replay_data_length,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        if replay_data_length < 0 or pos + replay_data_length > len(buf):
            return None
        pos += replay_data_length      # opaque LZMA replay data
        pos += 8                       # online/score id (long)
        # A trailing 4-byte LE length + LZMA1 blob = lazer's ScoreInfo.
        # Stable .osr have no trailing data → EOF here is the normal signal.
        if pos + 4 > len(buf):
            return None
        (length,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        if length <= 0 or pos + length > len(buf):
            return None
        compressed = buf[pos:pos + length]
        try:
            decompressed = lzma.decompress(compressed, format=lzma.FORMAT_ALONE)
        except lzma.LZMAError:
            decompressed = lzma.decompress(compressed, format=lzma.FORMAT_AUTO)
        return json.loads(decompressed.decode("utf-8"))
    except (struct.error, ValueError, lzma.LZMAError,
            json.JSONDecodeError, UnicodeDecodeError, IndexError) as e:
        log.debug("lazer ScoreInfo parse failed path=%s err=%s", osr_path, e)
        return None


def read_lazer_mods(osr_path: Path) -> list[dict] | None:
    """The lazer mod list as normalised ``{"acronym": str, "settings": dict}``
    entries (``settings`` is ``{}`` when the mod carries none), or None when no
    ScoreInfo blob is present. Unlike :func:`read_lazer_mod_acronyms` this
    PRESERVES each mod's ``settings`` object — the per-mod configuration lazer
    serialises for configurable mods such as Difficulty Adjust (DA), custom-rate
    Double Time, etc. An empty list means the blob was read but carried no
    mods (a nomod lazer play)."""
    info = parse_lazer_score_info(osr_path)
    if info is None:
        return None
    mods = info.get("mods")
    if not isinstance(mods, list):
        return []
    out: list[dict] = []
    for m in mods:
        if isinstance(m, dict):
            ac = m.get("acronym")
            if isinstance(ac, str):
                s = m.get("settings")
                out.append({"acronym": ac,
                            "settings": s if isinstance(s, dict) else {}})
        elif isinstance(m, str):
            out.append({"acronym": m, "settings": {}})
    return out


def read_lazer_mod_acronyms(osr_path: Path) -> list[str] | None:
    """The lazer mod acronyms (e.g. ``["HD", "DT", "CL"]``) from a .osr, or
    None when no ScoreInfo blob is present. An empty list means the blob was
    read but carried no mods (a nomod lazer play)."""
    mods = read_lazer_mods(osr_path)
    if mods is None:
        return None
    return [m["acronym"] for m in mods]


# --- Difficulty Adjust (DA) --------------------------------------------------
# osu.Game.Rulesets.Osu/Mods/OsuModDifficultyAdjust.cs +
# osu.Game/Rulesets/Mods/ModDifficultyAdjust.cs — the DA mod overrides the
# beatmap's AR/CS/OD/HP with per-setting values. Each SettingSource serialises
# to a snake_case key in the ScoreInfo ``settings`` object:
#   approach_rate  (ApproachRate,  MinValue 0 / MaxValue 10, Extended -10..11)
#   circle_size    (CircleSize,    0..10, ExtendedMaxValue 11)
#   overall_difficulty (OverallDifficulty, 0..10, ExtendedMaxValue 11)
#   drain_rate     (DrainRate,     0..10, ExtendedMaxValue 11)
#   extended_limits (ExtendedLimits BindableBool — ApplyLimits(extended))
# An ABSENT key means "keep the beatmap's value" (lazer's
# ReadCurrentFromDifficulty baseline: ``if (X.Value != null) difficulty.X =
# X.Value.Value``). DA sets the BASE stat, so rate mods (DT/HT) still scale it
# on top exactly as they would a beatmap stat.
DA_ACRONYM = "DA"
_DA_AR_KEY = "approach_rate"
_DA_CS_KEY = "circle_size"
_DA_OD_KEY = "overall_difficulty"
_DA_HP_KEY = "drain_rate"
_DA_EXT_KEY = "extended_limits"


@dataclass(frozen=True)
class DifficultyAdjust:
    """The DA overrides read from a .osr. Each of ar/cs/od/hp is the custom
    value or None (= keep the beatmap's stat). ``extended_limits`` mirrors the
    mod's ExtendedLimits toggle, which lifts the 0..10 clamp to 0..11 (AR to
    -10) so beyond-limits stats keep osu's linear AR->preempt extrapolation."""
    ar: float | None = None
    cs: float | None = None
    od: float | None = None
    hp: float | None = None
    extended_limits: bool = False

    @property
    def is_empty(self) -> bool:
        """True when the mod carried NO stat overrides (all four absent) — a
        no-op DA that must leave the beatmap difficulty untouched."""
        return (self.ar is None and self.cs is None
                and self.od is None and self.hp is None)


def _da_num(settings: dict, key: str) -> float | None:
    """A numeric DA setting or None (absent / non-numeric). JSON booleans are
    rejected so a stray ``true`` never reads back as a difficulty of 1.0."""
    v = settings.get(key)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def difficulty_adjust_from_mods(mods: list[dict]) -> "DifficultyAdjust | None":
    """The Difficulty Adjust overrides from an already-parsed
    :func:`read_lazer_mods` list, or None when no DA mod is present."""
    for m in mods:
        if isinstance(m, dict) and str(m.get("acronym", "")).upper() == DA_ACRONYM:
            s = m.get("settings") or {}
            return DifficultyAdjust(
                ar=_da_num(s, _DA_AR_KEY),
                cs=_da_num(s, _DA_CS_KEY),
                od=_da_num(s, _DA_OD_KEY),
                hp=_da_num(s, _DA_HP_KEY),
                extended_limits=bool(s.get(_DA_EXT_KEY, False)),
            )
    return None


def read_difficulty_adjust(osr_path: Path) -> "DifficultyAdjust | None":
    """The Difficulty Adjust overrides from a .osr, or None when the replay
    carries no DA mod (or no ScoreInfo blob at all). Never raises."""
    mods = read_lazer_mods(osr_path)
    if not mods:
        return None
    return difficulty_adjust_from_mods(mods)


def has_classic_mod(osr_path: Path) -> bool:
    """True iff the .osr's lazer mod list contains the Classic mod (CL)."""
    acs = read_lazer_mod_acronyms(osr_path)
    return bool(acs) and any(a.upper() == "CL" for a in acs)


# --- Rate-adjust mods: custom-rate DT/NC/HT/DC (speed_change) -----------------
# osu.Game/Rulesets/Mods/ModRateAdjust.cs — the abstract base every rate mod
# derives from. It exposes a configurable ``SpeedChange`` BindableNumber and
# ``ApplyToRate(time, rate) => rate * SpeedChange.Value`` — the ONE clock rate
# that scales the whole play (gameplay clock + AR/OD windows + audio).
#
#   * ModDoubleTime.cs — SpeedChange ``new BindableDouble(1.5){MinValue=1.01,
#     MaxValue=2, Precision=0.01}``. ApplyToTrack via RateAdjustModHelper =
#     AdjustableProperty.Tempo → tempo change, pitch PRESERVED.
#   * ModHalfTime.cs  — SpeedChange ``new BindableDouble(0.75){MinValue=0.5,
#     MaxValue=0.99, Precision=0.01}``. Tempo-only, pitch preserved.
#   * ModNightcore.cs / ModDaycore.cs — subclass DT/HT (same SpeedChange
#     default+range) but override ApplyToTrack to add BOTH a Frequency and a
#     Tempo adjustment: ``freqAdjust = SpeedChange.Default`` (pitch pinned to
#     the CLASSIC 1.5×/0.75× nightcore/daycore frequency), ``tempoAdjust =
#     value / SpeedChange.Default`` (tempo carries the remainder). Net rate =
#     freq×tempo = value, but the audio PITCHES (whereas DT/HT do not).
#
# The legacy 32-bit `mods` bitmask can encode DT/HT/NC as fixed 1.5×/0.75×
# ONLY — a per-play custom rate lives solely in the ScoreInfo blob settings
# under the snake_case key ``speed_change`` (SettingSource "Speed increase"/
# "Speed decrease"). Absent = the mod's default.
_SPEED_CHANGE_KEY = "speed_change"

# acronym -> default SpeedChange (== the fixed legacy bitmask rate)
RATE_ADJUST_DEFAULTS = {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}
# acronym -> (MinValue, MaxValue) the SettingSource clamps SpeedChange to
RATE_ADJUST_RANGE = {"DT": (1.01, 2.0), "NC": (1.01, 2.0),
                     "HT": (0.5, 0.99), "DC": (0.5, 0.99)}
# NC/DC change PITCH (Frequency); DT/HT are tempo-only (pitch preserved)
PITCH_RATE_MODS = frozenset({"NC", "DC"})
RATE_ADJUST_MODS = frozenset(RATE_ADJUST_DEFAULTS)


@dataclass(frozen=True)
class RateAdjust:
    """A rate-adjust mod (DT/NC/HT/DC) read from a .osr.

    ``speed`` is the effective clock rate (the mod's default when the play
    carries no custom ``speed_change``, else the setting clamped to the mod's
    [Min,Max]). ``pitch`` is True for NC/DC (their audio shifts pitch) and
    False for DT/HT (tempo-only). ``is_default`` is True when ``speed`` equals
    the mod's default — i.e. the play is a plain fixed-rate DT/NC/HT/DC that
    the legacy bitmask already renders, so no override is needed."""
    acronym: str
    speed: float
    pitch: bool
    is_default: bool


def rate_adjust_from_mods(mods: list[dict]) -> "RateAdjust | None":
    """The rate-adjust mod from an already-parsed :func:`read_lazer_mods`
    list, or None when no DT/NC/HT/DC mod is present. Only one rate mod can be
    active in a play, so the first match wins. ``speed_change`` is clamped to
    the mod's SettingSource range; a non-numeric value (or an absent key) falls
    back to the default."""
    for m in mods:
        if not isinstance(m, dict):
            continue
        ac = str(m.get("acronym", "")).upper()
        if ac in RATE_ADJUST_MODS:
            s = m.get("settings") or {}
            default = RATE_ADJUST_DEFAULTS[ac]
            raw = _da_num(s, _SPEED_CHANGE_KEY)  # numeric-or-None (bools -> None)
            if raw is None:
                speed = default
            else:
                lo, hi = RATE_ADJUST_RANGE[ac]
                speed = min(hi, max(lo, raw))
            return RateAdjust(acronym=ac, speed=speed,
                              pitch=ac in PITCH_RATE_MODS,
                              is_default=(speed == default))
    return None


def read_rate_adjust(osr_path: Path) -> "RateAdjust | None":
    """The rate-adjust mod (DT/NC/HT/DC + effective speed_change) from a .osr,
    or None when the replay carries no rate mod (or no ScoreInfo blob). Never
    raises."""
    mods = read_lazer_mods(osr_path)
    if not mods:
        return None
    return rate_adjust_from_mods(mods)
