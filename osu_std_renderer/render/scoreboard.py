"""In-game leaderboard — §4.6 Gameplay.ScoreBoard: ACCEPTED + NO-OP.

The `show_scoreboard` / `scoreboard_avatars` preset keys are stored,
validated and honestly documented, but nothing is drawn yet: a live
scoreboard needs the map's top-50 (osu! API v2 `GET
/beatmaps/{id}/scores`) which only the SERVICE holds credentials for.
The agreed integration (future bot work): the r3d_render worker fetches
the leaderboard, writes it next to the replay as JSON, and passes
`--scoreboard-json <path>`; the engine then renders the stable-style
left-edge score panels (avatars optional, gated on scoreboard_avatars).

THIS MODULE IS THE LOADER STUB for that hand-off — the JSON contract is
fixed NOW so the bot side can build against it:

    [
      {"username": "...", "score": 123456789, "combo": 1234,
       "rank": 1, "avatar_png": "path-or-null", "mods": 64},
      ...
    ]

load_scoreboard_json() parses exactly that (fail-soft: any problem →
None, the scoreboard simply stays off). draw() intentionally raises
NotImplementedError so nobody wires half a feature silently.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScoreboardEntry:
    username: str
    score: int
    combo: int
    rank: int
    avatar_png: str | None = None
    mods: int = 0


def load_scoreboard_json(path: Path | None) -> list[ScoreboardEntry] | None:
    """Parse the bot-provided leaderboard JSON (contract above), sorted by
    rank. Fail-soft: missing/invalid file → None (scoreboard off)."""
    if path is None:
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = [ScoreboardEntry(
            username=str(e["username"]), score=int(e["score"]),
            combo=int(e.get("combo", 0)), rank=int(e.get("rank", i + 1)),
            avatar_png=e.get("avatar_png"), mods=int(e.get("mods", 0)))
            for i, e in enumerate(raw)]
        return sorted(entries, key=lambda e: e.rank)
    except Exception as e:  # noqa: BLE001 — never kill a render over this
        print(f"WARNING: scoreboard JSON unreadable ({e}) — scoreboard off",
              file=sys.stderr)
        return None


def draw(*_args, **_kwargs) -> None:
    """Rendering lands with the bot integration — loudly unimplemented so
    a half-wire can't ship silently."""
    raise NotImplementedError(
        "scoreboard rendering needs the osu!API leaderboard hand-off "
        "(see module docstring for the JSON contract)")
