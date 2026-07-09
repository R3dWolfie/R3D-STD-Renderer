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


def read_lazer_mod_acronyms(osr_path: Path) -> list[str] | None:
    """The lazer mod acronyms (e.g. ``["HD", "DT", "CL"]``) from a .osr, or
    None when no ScoreInfo blob is present. An empty list means the blob was
    read but carried no mods (a nomod lazer play)."""
    info = parse_lazer_score_info(osr_path)
    if info is None:
        return None
    mods = info.get("mods")
    if not isinstance(mods, list):
        return []
    out: list[str] = []
    for m in mods:
        if isinstance(m, dict):
            ac = m.get("acronym")
            if isinstance(ac, str):
                out.append(ac)
        elif isinstance(m, str):
            out.append(m)
    return out


def has_classic_mod(osr_path: Path) -> bool:
    """True iff the .osr's lazer mod list contains the Classic mod (CL)."""
    acs = read_lazer_mod_acronyms(osr_path)
    return bool(acs) and any(a.upper() == "CL" for a in acs)
