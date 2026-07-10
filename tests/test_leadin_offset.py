"""Regression tests for the osu!stable replay-clock lead-in fix.

osu!stable replays begin with up to two (256, -500) placeholder frames; osu!'s
LegacyScoreDecoder accumulates their time deltas into the running clock before
dropping them, so the second placeholder's delta is the intro-skip / audio
lead-in. osrparse strips those frames WITHOUT accumulating the deltas, so the
lead-in never reaches ``Replay.replay_data``. ``parse_replay`` recovers it from
the raw blob (``_recover_leadin_offset``) and seeds the clock with it, exactly
reproducing osu!'s origin.

These build synthetic .osr files with a KNOWN injected lead-in and assert the
parsed frame times land at the osu!-faithful absolute clock, and that replays
with no placeholder frames (lazer / clean stable) are a strict no-op.
"""
import lzma
import struct
import tempfile
from pathlib import Path

from osu_std_renderer.replay.replay import (_recover_leadin_offset,
                                            parse_replay)


# --------------------------------------------------------------------------- #
# minimal .osr writer (matches osrparse's unpack() field order exactly)
# --------------------------------------------------------------------------- #
def _uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _ostr(s: str) -> bytes:
    if s == "":
        return b"\x00"
    raw = s.encode("utf-8")
    return b"\x0b" + _uleb(len(raw)) + raw


def _build_osr(frame_str: str, *, game_version: int = 20240101,
               mods: int = 0) -> Path:
    """Serialise a valid stable .osr whose LZMA replay-data decompresses to
    ``frame_str`` (a comma-separated ``w|x|y|z`` stream with trailing comma)."""
    comp = lzma.compress(frame_str.encode("ascii"), format=lzma.FORMAT_ALONE)
    out = bytearray()
    out += struct.pack("<b", 0)                 # mode = std
    out += struct.pack("<i", game_version)      # game version
    out += _ostr("0" * 32)                      # beatmap md5
    out += _ostr("tester")                      # player name
    out += _ostr("0" * 32)                      # replay md5
    out += struct.pack("<6H", 300, 0, 0, 0, 0, 0)  # 300/100/50/geki/katu/miss
    out += struct.pack("<i", 1000)              # score
    out += struct.pack("<H", 10)                # max combo
    out += struct.pack("<b", 1)                 # perfect
    out += struct.pack("<i", mods)              # mods
    out += _ostr("")                            # life-bar graph (empty)
    out += struct.pack("<q", 0)                 # timestamp
    out += struct.pack("<i", len(comp))         # replay-data length
    out += comp                                 # LZMA replay data
    out += struct.pack("<q", 0)                 # replay id (long)
    d = Path(tempfile.mkdtemp(prefix="stdleadin_"))
    p = d / "synthetic.osr"
    p.write_bytes(bytes(out))
    return p


def _sentinels(lead: int) -> str:
    # the two (256,-500) placeholders osu! accumulates but osrparse strips
    return f"0|256|-500|0,{lead}|256|-500|0,"


_SEED = "-12345|0|0|9999,"


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_recover_leadin_type_a_small_first_delta():
    """Type A (e.g. Pekorap): the whole intro-skip lives on the 2nd placeholder
    (+2000), the first gameplay frame has a small delta. osu! origin = 2000."""
    gp = "5|100|100|0,16|110|110|0,16|120|120|0,"
    p = _build_osr(_sentinels(2000) + gp + _SEED)
    assert _recover_leadin_offset(p) == 2000
    frames, meta = parse_replay(p)
    # gameplay clock = 2000 (lead-in) + running gameplay deltas 5,16,16
    assert [f.time_ms for f in frames] == [2005, 2021, 2037]
    assert (frames[0].x, frames[0].y) == (100.0, 100.0)


def test_recover_leadin_type_b_large_negative_cancel():
    """Type B (e.g. Kimi ga Tame / Pretty Little Psycho): the intro-skip
    (+8000) is *cancelled* by a large-negative first gameplay delta (-7995) so
    the true origin is ~5 ms. The removed `-5000` heuristic used to zero that
    -7995, which — with the lead-in now seeded — would wrongly land the clock
    at 8000 and mass-miss. Faithful accumulation must give origin 5."""
    gp = "-7995|100|100|0,16|110|110|0,16|120|120|0,"
    p = _build_osr(_sentinels(8000) + gp + _SEED)
    assert _recover_leadin_offset(p) == 8000
    frames, _ = parse_replay(p)
    assert [f.time_ms for f in frames] == [5, 21, 37]


def test_no_placeholder_frames_is_noop():
    """A replay whose stream has no (256,-500) placeholders (lazer replays, and
    the general no-skip case) recovers a 0 lead-in and parses byte-identically
    to naive accumulation — the fix is a strict no-op."""
    gp = "0|100|100|0,16|110|110|0,16|120|120|0,"
    p = _build_osr(gp + _SEED)
    assert _recover_leadin_offset(p) == 0
    frames, _ = parse_replay(p)
    assert [f.time_ms for f in frames] == [0, 16, 32]


def test_clean_stable_minus_one_leadin():
    """Clean osu!stable plays carry placeholders with a -1 ms lead-in (the
    real, osu!-faithful origin). Recovery returns -1 and shifts the clock by
    exactly that — no collapse."""
    gp = "20|100|100|0,16|110|110|0,"
    p = _build_osr(_sentinels(-1) + gp + _SEED)
    assert _recover_leadin_offset(p) == -1
    frames, _ = parse_replay(p)
    # -1 (lead-in) + 20, then +16; first frame clamped by max(t,0) is 19 here
    assert [f.time_ms for f in frames] == [19, 35]


def test_single_leading_placeholder_only():
    """If only the first frame is a (256,-500) placeholder (delta 0) and the
    second is already gameplay, osrparse keeps the gameplay frame; lead-in is
    0 and its own delta is accumulated normally."""
    stream = "0|256|-500|0,30|100|100|0,16|110|110|0," + _SEED
    p = _build_osr(stream)
    assert _recover_leadin_offset(p) == 0
    frames, _ = parse_replay(p)
    assert [f.time_ms for f in frames] == [30, 46]


def test_recover_leadin_failsoft_on_garbage():
    """Any decode problem must fail soft to 0 (pre-fix behaviour), never raise."""
    d = Path(tempfile.mkdtemp(prefix="stdleadin_"))
    junk = d / "junk.osr"
    junk.write_bytes(b"\x00\x01\x02not a real replay")
    assert _recover_leadin_offset(junk) == 0
    assert _recover_leadin_offset(d / "does_not_exist.osr") == 0


def run() -> None:
    test_recover_leadin_type_a_small_first_delta()
    test_recover_leadin_type_b_large_negative_cancel()
    test_no_placeholder_frames_is_noop()
    test_clean_stable_minus_one_leadin()
    test_single_leading_placeholder_only()
    test_recover_leadin_failsoft_on_garbage()


if __name__ == "__main__":
    run()
    print("ok")
