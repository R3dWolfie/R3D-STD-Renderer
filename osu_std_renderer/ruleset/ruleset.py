"""Ruleset (judgment simulation) — STUB. RENDER_PLAN.md §5.5
(app/rulesets/osu/ruleset.go) semantics documented for the build-out phase.

WHAT THE REAL RULESET DOES (port targets):

  Hit windows (all half-windows, ms, from §2.4 — already computed in
  beatmap/difficulty.py as hit300_u/hit100_u/hit50_u):
      |Δt| <= Hit300 → 300 ; <= Hit100 → 100 ; <= Hit50 → 50 ; else miss.
      An object becomes hittable inside HittableRange (400 ms); clicks
      earlier than the 50 window "shake" (no judgment).

  Click routing / notelock (`CanBeHitStable` / `CanBeHitLazer`):
      stable: a click is eaten by the earliest unjudged clickable object;
      objects later in order cannot be hit while an earlier one is alive
      (clicking one while a previous is unjudged → shake, no hit). Misses
      unlock at the miss window's close.

  Per-frame replay walk (§5.5): every intermediate integer millisecond is
  replayed; key-down EDGES trigger UpdateClickFor (judgments), held keys
  feed UpdateNormalFor (slider follow), UpdatePostFor closes windows.

  Sliders: head judged like a circle (window on start time); while the
  correct key is held and the cursor stays inside the follow circle
  (radius = 2.4 × CircleRadius, lazer/stable identical multiplier), ticks
  and repeats score; leaving follow state mid-slider = sliderbreak (combo
  reset, not a miss count); end counted if followed at the LegacyLastTick.
      Slider scoring aggregation → 300 (all parts) / 100 (>=half) /
      50 (any) / miss (none).

  Spinners: rotation accumulated from per-ms cursor angle deltas around
  the centre; SpinnerRatio (§2.4) converts rotations→required spins;
  RPM display = rotations in the last second × 60.

  Score/combo/HP/PP: ScoreV1 (300+bonus per combo), HP drain per
  §2.4 HPMod, PP via rosu-pp at the end (the service already computes PP
  out-of-band — mania's pp.py pattern).

STRATEGY FOR THIS ENGINE (proven in 3 shipped modes): the sim does NOT
need to be perfect — `reconcile_to_counts` (mania v2 judgments.py) snaps
the live 300/100/50/miss tallies to the replay's authoritative final
counts, so sim imperfection never changes the displayed numbers, only
(rarely) their timing.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JudgmentKind(Enum):
    HIT300 = "300"
    HIT100 = "100"
    HIT50 = "50"
    MISS = "miss"
    SLIDER_TICK = "tick"
    SLIDER_REPEAT = "repeat"
    SLIDER_END = "end"
    SLIDER_BREAK = "sliderbreak"
    SPINNER_SPIN = "spin"
    SPINNER_BONUS = "bonus"


@dataclass(frozen=True)
class JudgmentEvent:
    """One judgment the HUD/playfield consumes: popup at (x, y) osu!px."""
    time_ms: float
    kind: JudgmentKind
    object_id: int
    x: float
    y: float
    combo_after: int
    score_after: int


class Ruleset:
    """STUB — judgment simulation is a later phase (Phase 1 of APPROACH.md
    §4). The scaffold parses everything the sim needs (windows, follow
    radius, tick times, per-ms replay frames); this class defines the
    interface the record pipeline will drive.
    """

    FOLLOW_CIRCLE_RADIUS_MULT = 2.4  # × CircleRadius while sliding

    def __init__(self, beatmap, frames, meta):
        self.beatmap = beatmap
        self.frames = frames
        self.meta = meta

    def update_click_for(self, time_ms: int, x: float, y: float, keys: int):
        raise NotImplementedError("ruleset ships in Phase 1 (see module docstring)")

    def update_normal_for(self, time_ms: int, x: float, y: float, keys: int):
        raise NotImplementedError("ruleset ships in Phase 1 (see module docstring)")

    def update_post_for(self, time_ms: int):
        raise NotImplementedError("ruleset ships in Phase 1 (see module docstring)")

    def reconcile_to_counts(self):
        """Snap live tallies to the replay's final counts (mania v2
        judgments.py pattern — the load-bearing feasibility trick)."""
        raise NotImplementedError("ruleset ships in Phase 1 (see module docstring)")
