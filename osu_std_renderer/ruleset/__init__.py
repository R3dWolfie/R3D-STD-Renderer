"""Judgment simulation — replay-driven hit results (see ruleset.py for the
ported-from-ppy/osu logic and the stable-vs-lazer choices) + the HP model
(health.py — lazer's OsuHealthProcessor/DrainingHealthProcessor port)."""
from .health import HealthTimeline, compute_drain_rate  # noqa: F401
from .ruleset import (  # noqa: F401
    BASE_LARGE_TICK,
    BASE_SCORE,
    FOLLOW_CIRCLE_RADIUS_MULT,
    MISS_WINDOW,
    NOTELOCK_END_LENIENCY,
    TAIL_LENIENCY,
    HeldState,
    JudgmentEvent,
    JudgmentKind,
    ObjectVerdict,
    OsuHitWindows,
    PartOutcome,
    Press,
    Ruleset,
    SimResult,
    SliderRecord,
    StdRuleset,
    press_edges,
)
