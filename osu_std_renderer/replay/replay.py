"""std .osr decode + 2D cursor interpolation — adapted from the production
catch renderer's replay.py (osu-catch/osu_catch_renderer/replay.py), extended
from 1D catcher-x to the std (x, y, keys) frame.

RENDER_PLAN.md §5.5 — the replay→cursor contract the ruleset phase must obey
(documented here; the full ruleset ships in a later phase):

  * the .osr is parsed by osrparse (reference used rplpa); the RNG seed
    frame (time_delta == -12345) is stripped;
  * playback replays EVERY INTERMEDIATE INTEGER MILLISECOND so low draw
    rates never skip frames: per tick, while
    `replayTime + frames[idx].Time <= floor(nTime)` advance the index and
    apply cursor.SetPos(MouseX, MouseY) + the key bitfield (feeding
    UpdateClickFor/UpdateNormalFor/UpdatePostFor for judgments);
  * when no frame is consumed on a tick, the cursor position is
    INTERPOLATED between the neighbouring frames (cursor_at below);
  * Relax/Autopilot get synthesized clicks/aim.

Frame times are in MAP time (rate mods don't compress them), so they line
up 1:1 with beatmap object times — same finding as catch.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from osrparse import Replay

# osrparse seeds the last frame with this sentinel time_delta (RNG seed).
_SEED_DELTA = -12345

# std key bitmask per frame (osu! wiki / osr format)
KEY_M1 = 1
KEY_M2 = 2
KEY_K1 = 4
KEY_K2 = 8
KEY_SMOKE = 16


class ReplayParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class StdFrame:
    """One replay frame: cursor position in osu!px (512×384) + key bitmask."""
    time_ms: int
    x: float
    y: float
    keys: int

    @property
    def pressed(self) -> bool:
        return bool(self.keys & (KEY_M1 | KEY_M2 | KEY_K1 | KEY_K2))


@dataclass(frozen=True)
class ReplayMeta:
    mode: int
    beatmap_md5: str
    player_name: str
    mods: int
    score: int
    max_combo: int
    count_300: int
    count_100: int
    count_50: int
    count_geki: int
    count_katu: int
    count_miss: int
    accuracy: float
    grade: str
    game_version: int = 0    # <30000000 = osu!stable, else lazer
    death_ms: int | None = None
    played_at: str = ""      # .osr timestamp → "Played on <date>" (results)


def parse_replay(path: Path) -> tuple[list[StdFrame], ReplayMeta]:
    path = Path(path)
    if not path.exists():
        raise ReplayParseError(f"replay not found: {path}")
    try:
        r = Replay.from_path(path)
    except Exception as e:  # noqa: BLE001 - osrparse raises bare exceptions
        raise ReplayParseError(f"osrparse failed: {e}") from e

    frames: list[StdFrame] = []
    t = 0
    first = True
    for ev in r.replay_data or []:
        delta = int(getattr(ev, "time_delta", 0))
        if delta == _SEED_DELTA:
            continue  # RNG seed frame, not a real position
        if first:
            first = False
            # osu! sometimes prefixes a garbage placeholder frame with a huge
            # negative delta that would shift the whole timeline; a normal
            # AudioLeadIn is only ~1-2 s, so anything beyond that is the
            # placeholder — ignore its jump. (Catch-proven heuristic.)
            if delta < -5000:
                delta = 0
        t += delta
        x = getattr(ev, "x", None)
        y = getattr(ev, "y", None)
        if x is None or y is None:
            continue  # non-positional frame
        keys = getattr(ev, "keys", 0)
        try:
            keys = int(keys)
        except (TypeError, ValueError):
            keys = 0
        frames.append(StdFrame(time_ms=max(t, 0), x=float(x), y=float(y), keys=keys))
    frames.sort(key=lambda f: f.time_ms)

    # std accuracy: 300/100/50 weighted (300 = full weight)
    total = r.count_300 + r.count_100 + r.count_50 + r.count_miss
    if total > 0:
        acc = (300 * r.count_300 + 100 * r.count_100 + 50 * r.count_50) / (300 * total)
    else:
        acc = 1.0

    # fail detection from the life-bar graph (NoFail exempt) — catch-proven
    death_ms: int | None = None
    NF = 0x1
    if not (int(r.mods) & NF):
        for e in (getattr(r, "life_bar_graph", None) or []):
            try:
                if float(e.life) <= 0.001:
                    death_ms = int(e.time)
                    break
            except (TypeError, ValueError, AttributeError):
                continue

    # play date from the .osr timestamp (osrparse → datetime); "" fail-soft
    played_at = ""
    ts = getattr(r, "timestamp", None)
    if ts is not None:
        try:
            played_at = ts.strftime("%d %b %Y")
        except Exception:  # noqa: BLE001 — odd/naive datetime → skip
            played_at = str(ts)[:10]

    meta = ReplayMeta(
        mode=int(r.mode.value if hasattr(r.mode, "value") else r.mode),
        beatmap_md5=str(getattr(r, "beatmap_hash", "") or ""),
        player_name=r.username,
        mods=int(r.mods),
        score=int(r.score),
        max_combo=int(r.max_combo),
        count_300=int(r.count_300),
        count_100=int(r.count_100),
        count_50=int(r.count_50),
        count_geki=int(getattr(r, "count_geki", 0) or 0),
        count_katu=int(getattr(r, "count_katu", 0) or 0),
        count_miss=int(r.count_miss),
        accuracy=round(acc * 100, 2),
        grade=_grade(r),
        game_version=int(getattr(r, "game_version", 0) or 0),
        death_ms=death_ms,
        played_at=played_at,
    )
    return frames, meta


def cursor_at(frames: list[StdFrame], t_ms: float) -> tuple[float, float, int]:
    """Linearly interpolate the cursor at t_ms → (x, y, keys).

    2D extension of catch's catcher_x_at: binary search + lerp. Keys are NOT
    interpolated — the frame at-or-after t carries the held state (key
    edges are only meaningful at exact frame times; the §5.5 per-ms walk in
    the ruleset consumes frames directly, this is for DRAWING)."""
    if not frames:
        return 256.0, 192.0, 0
    if t_ms <= frames[0].time_ms:
        f = frames[0]
        return f.x, f.y, f.keys
    if t_ms >= frames[-1].time_ms:
        f = frames[-1]
        return f.x, f.y, f.keys
    lo, hi = 0, len(frames) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if frames[mid].time_ms < t_ms:
            lo = mid + 1
        else:
            hi = mid
    a, b = frames[lo - 1], frames[lo]
    span = b.time_ms - a.time_ms
    if span <= 0:
        return b.x, b.y, b.keys
    f = (t_ms - a.time_ms) / span
    return a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, b.keys


def _grade(r) -> str:
    """osu!(lazer)-EXACT rank from the replay counts. Ported from
    ScoreProcessor.RankFromScore (accuracy cutoffs X=1/S=.95/A=.9/B=.8/
    C=.7/D=0) + OsuScoreProcessor's std override (a Miss caps S/X at A).
    HD/FL silver variants are a display concern, applied elsewhere."""
    c300, c100, c50, miss = (int(r.count_300), int(r.count_100),
                             int(r.count_50), int(r.count_miss))
    total = c300 + c100 + c50 + miss
    if total == 0:
        return "D"
    acc = (300 * c300 + 100 * c100 + 50 * c50) / (300.0 * total)
    if acc >= 1.0:
        rank = "SS"
    elif acc >= 0.95:
        rank = "S"
    elif acc >= 0.9:
        rank = "A"
    elif acc >= 0.8:
        rank = "B"
    elif acc >= 0.7:
        rank = "C"
    else:
        rank = "D"
    if miss > 0 and rank in ("S", "SS"):
        rank = "A"
    return rank
