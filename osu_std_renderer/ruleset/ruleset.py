"""osu!standard judgment simulation — replay-driven hit results,
reconciled to the replay's authoritative counts.

PORTED LOGIC (MIT, ppy/osu master — file+class cited per function):
    osu.Game/Rulesets/Scoring/HitWindows.cs                    HitWindows.ResultFor/CanBeHit
    osu.Game.Rulesets.Osu/Scoring/OsuHitWindows.cs             window ranges, floor()-0.5, MISS_WINDOW=400
    osu.Game.Rulesets.Osu/UI/LegacyHitPolicy.cs                stable notelock (CheckHittable)
    osu.Game.Rulesets.Osu/Objects/Drawables/DrawableHitCircle.cs   CheckForResult (click + window-close miss)
    osu.Game.Rulesets.Osu/Objects/Drawables/DrawableSlider.cs      classic slider aggregation
    osu.Game.Rulesets.Osu/Objects/Drawables/DrawableSliderBall.cs  FOLLOW_AREA = 2.4
    osu.Game.Rulesets.Osu/Objects/Drawables/SliderInputManager.cs  follow-area tracking
    osu.Game/Rulesets/Objects/SliderEventGenerator.cs          TAIL_LENIENCY=-36, legacy-last-tick time
    osu.Game.Rulesets.Osu/Objects/Drawables/DrawableSpinner.cs     spinner result tiers

STABLE-vs-LAZER CHOICES — the engine is AUTO-DETECTED from the .osr's
game_version (< 30000000 = stable, else lazer; overridable):
  * STABLE notelock = LegacyHitPolicy ("classic note lock"): the earliest
    unjudged clickable eats the click; clicking a later object while an
    earlier one is still hittable shakes and does nothing (misses unlock
    at the window close — this cascades on early taps, faithfully).
  * LAZER notelock = StartTimeOrderedHitPolicy: a hit on a later object is
    allowed once the previous object's start time has passed and
    force-misses every earlier unjudged circle/head at that moment
    (no cascades). Trivially-parallel port, selected for lazer replays.
  * LAZER sliders: heads are TIMING-judged (Great/Ok/Meh by delta — those
    are what the .osr's 300/100/50 counts contain); a missed head IS a
    miss count; ticks/tails are separate non-counted judgments (tracked
    for combo/visuals). The stable classic aggregate below only applies
    to stable replays.
  * sliders = ClassicSliderBehaviour (stable): a head hit anywhere in the
    meh (50) window keeps combo and starts tracking; the slider's single
    displayed judgment aggregates its parts (all hit → 300, ≥half → 100,
    ≥1 → 50, none → miss). The THREE slider-part miss kinds are distinct
    (different semantics, tracked separately in SliderRecord + logs) —
    the TAIL is the ONLY lenient part:
      1. HEAD miss  — no valid click in the head window: sliderbreak —
         combo RESETS at the head's miss moment (window close), the part
         is lost, but it does NOT increment the miss count and shows no
         miss sprite at the head (the slider's final result reflects it).
         Logged `head_missed@t`.
      2. TICK/REPEAT miss — tracking broken as a tick/repeat passes: the
         classic SLIDERBREAK. Combo resets to 0 at that moment; the part
         is lost; NOT a miss count. Repeats are tick-equivalent for all
         combo/result math. Logged `sliderbreak@t`.
      3. TAIL miss  — not tracking at the (legacy-last-tick-judged) tail:
         stable's lenient end. NOT a sliderbreak, does NOT reset combo;
         only downgrades the aggregate. Logged `tail_missed@t`.
  * tracking key restriction: lazer's timeToAcceptAnyKeyAfter (the Z→Z+X
    edge case) is simplified to "any key held" — stable's own behaviour.
  * lazer's PostProcessHeadJudgement retro-hit of ticks passed through
    before a LATE head hit is not simulated (rare: only ticks inside the
    head's 50-window); reconcile absorbs any resulting count drift.

SIMPLIFICATIONS (logged, reconcile keeps totals exact):
  * spinners: rotations accumulated from per-frame cursor angle deltas
    while a key is held; required spins = duration_s × SpinnerRatio (§2.4);
    result tiers per DrawableSpinner (progress ≥1 → 300, >0.9 → 100,
    >0.75 → 50, else miss). No spin-bonus score events.
  * HP drain: deferred entirely.
  * score: lazer "standardised" shape (ScoreProcessor semantics, same
    formula versus_telemetry ships):
        500000·acc·(comboPortion/maxComboPortion)
      + 500000·acc⁵·(judgedBase/maxBase),  comboPortion += base·√combo
    documented as an approximation until the HUD phase pins it.

SAFETY NET: reconcile_to_counts (the mania v2 judgments.py pattern) snaps
the final 300/100/50/miss tallies to the .osr's recorded counts, so the
displayed numbers ALWAYS equal the replay's real results; the pre-reconcile
delta is reported as the sim-accuracy metric.
"""
from __future__ import annotations

import bisect
import math
import sys
from dataclasses import dataclass, field
from enum import Enum

from ..beatmap.objects import Slider, Spinner
from ..replay.replay import KEY_K1, KEY_K2, KEY_M1, KEY_M2, cursor_at

# --- constants (sources cited) -------------------------------------------------
FOLLOW_CIRCLE_RADIUS_MULT = 2.4     # DrawableSliderBall.FOLLOW_AREA
TAIL_LENIENCY = -36.0               # SliderEventGenerator.TAIL_LENIENCY
NOTELOCK_END_LENIENCY = 3.0         # LegacyHitPolicy "3ms of extra leniency"
MISS_WINDOW = 400.0                 # OsuHitWindows.MISS_WINDOW (== HittableRange)

# lazer ScoreProcessor.GetBaseScoreForResult (Great/Ok/Meh/LargeTickHit)
BASE_SCORE = {"300": 300, "100": 100, "50": 50, "miss": 0}
BASE_LARGE_TICK = 30
COMBO_EXPONENT = 0.5                # ScoreProcessor.COMBO_EXPONENT


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
    """One object-level judgment popup the scene/HUD consumes: shown at
    (x, y) osu!px from time_ms (ResultFadeIn 120 / ResultFadeOut 600)."""
    time_ms: float
    kind: JudgmentKind
    object_id: int
    x: float
    y: float
    combo_after: int
    score_after: int
    acc_after: float = 1.0


# --- hit windows ----------------------------------------------------------------

def _difficulty_range(diff: float, min_v: float, mid_v: float, max_v: float) -> float:
    """IBeatmapDifficultyInfo.DifficultyRange (ppy/osu
    osu.Game/Beatmaps/IBeatmapDifficultyInfo.cs) — plain double interpolation
    across OD 0/5/10. (difficulty.py's difficulty_rate is the danser/stable
    variant with the float32 cast; for hit windows the two differ < 1e-4 ms.)"""
    if diff > 5:
        return mid_v + (max_v - mid_v) * (diff - 5) / 5
    if diff < 5:
        return mid_v - (mid_v - min_v) * (5 - diff) / 5
    return mid_v


class OsuHitWindows:
    """Port of ppy/osu osu.Game.Rulesets.Osu/Scoring/OsuHitWindows.cs +
    the ResultFor/CanBeHit semantics of osu.Game/Rulesets/Scoring/HitWindows.cs.

    SetDifficulty: window = floor(DifficultyRange(od, range)) - 0.5 — the
    -0.5 on the floored value replicates stable's integer-window strict-<
    comparison for integer deltas. (difficulty.py's hit50/100/300 are the
    stable int-truncated values; they agree within 0.5 ms.)"""

    GREAT_RANGE = (80.0, 50.0, 20.0)
    OK_RANGE = (140.0, 100.0, 60.0)
    MEH_RANGE = (200.0, 150.0, 100.0)
    MISS_WINDOW = MISS_WINDOW

    def __init__(self, od: float):
        self.great = math.floor(_difficulty_range(od, *self.GREAT_RANGE)) - 0.5
        self.ok = math.floor(_difficulty_range(od, *self.OK_RANGE)) - 0.5
        self.meh = math.floor(_difficulty_range(od, *self.MEH_RANGE)) - 0.5

    def result_for(self, time_offset: float) -> JudgmentKind | None:
        """HitWindows.ResultFor: best window containing |offset|, walking
        Great→Meh; None when outside every scoring window."""
        a = abs(time_offset)
        if a <= self.great:
            return JudgmentKind.HIT300
        if a <= self.ok:
            return JudgmentKind.HIT100
        if a <= self.meh:
            return JudgmentKind.HIT50
        return None

    def can_be_hit(self, time_offset: float) -> bool:
        """HitWindows.CanBeHit: a future non-miss result is still possible
        (offset ≤ the lowest successful window = meh)."""
        return time_offset <= self.meh


# --- replay input digestion ------------------------------------------------------

# stable sets the M1/M2 bit alongside K1/K2, so keyboard+mouse conflate into
# two logical channels (same conflation the ppy OsuAction Left/RightButton do).
_CH1 = KEY_M1 | KEY_K1
_CH2 = KEY_M2 | KEY_K2


@dataclass(frozen=True)
class Press:
    """One click: a key/button channel going down (press EDGE — held keys
    never re-click)."""
    time_ms: float
    x: float
    y: float
    channel: int    # 1 = left (M1|K1), 2 = right (M2|K2)


def press_edges(frames) -> list[Press]:
    """Per §5.5 the replay walk feeds UpdateClickFor on key-DOWN edges only:
    a channel newly active this frame vs the previous frame is one click."""
    out: list[Press] = []
    prev = 0
    for f in frames:
        ch = (1 if f.keys & _CH1 else 0) | (2 if f.keys & _CH2 else 0)
        new = ch & ~prev
        if new & 1:
            out.append(Press(f.time_ms, f.x, f.y, 1))
        if new & 2:
            out.append(Press(f.time_ms, f.x, f.y, 2))
        prev = ch
    return out


class HeldState:
    """Key channels held at an arbitrary time: the last frame at-or-before t
    carries the held bitmask (edges only exist at frame times)."""

    def __init__(self, frames):
        self._times = [f.time_ms for f in frames]
        self._held = [(1 if f.keys & _CH1 else 0) | (2 if f.keys & _CH2 else 0)
                      for f in frames]

    def channels_at(self, t: float) -> int:
        i = bisect.bisect_right(self._times, t) - 1
        return self._held[i] if i >= 0 else 0

    def any_at(self, t: float) -> bool:
        return self.channels_at(t) != 0


# --- per-object sim state ---------------------------------------------------------

@dataclass
class PartOutcome:
    """One slider scoring part (for tick/repeat/tail visuals + combo)."""
    time: float                 # display/scoring time (tail displays at endTime)
    kind: str                   # "head" | "tick" | "repeat" | "tail"
    pos: tuple[float, float]    # stacked osu!px
    hit: bool
    # cursor-to-ball distance (osu!px) at this part's JUDGE time — the
    # per-part tracking quality the lazer tick/tail reconcile ranks by
    # (worst-tracked parts become the misses). inf for the head (unused).
    margin: float = math.inf


@dataclass
class SliderRecord:
    """Explicit per-slider part-result record (the telemetry/HUD layers
    consume this): the three miss kinds are semantically distinct — a
    head miss is a sliderbreak (combo reset at the head's miss moment,
    logged separately as head_missed), a tick/REPEAT miss is the classic
    sliderbreak (repeats are tick-equivalent), a tail miss is stable's
    lenient end (no reset, aggregate downgrade only). None of the three
    increments the miss count by itself."""
    head_hit: bool
    head_delta: float | None            # signed click error when hit
    head_missed_at: float | None        # head window close when missed (combo reset here)
    ticks: list[PartOutcome]            # ticks + repeats (tick-equivalent), time order
    tail_hit: bool
    tail_missed_at: float | None        # slider end time when missed
    sliderbreaks: list[float]           # tick/repeat miss times (combo resets)

    @classmethod
    def from_parts(cls, parts: list[PartOutcome], head_delta: float | None,
                   head_deadline: float) -> "SliderRecord":
        head = parts[0]
        ticks = [p for p in parts[1:] if p.kind in ("tick", "repeat")]
        tails = [p for p in parts[1:] if p.kind == "tail"]
        tail_hit = tails[0].hit if tails else True
        return cls(
            head_hit=head.hit,
            head_delta=head_delta if head.hit else None,
            head_missed_at=None if head.hit else head_deadline,
            ticks=ticks,
            tail_hit=tail_hit,
            tail_missed_at=(None if (tail_hit or not tails)
                            else tails[0].time),
            sliderbreaks=[p.time for p in ticks if not p.hit])


@dataclass
class ObjectVerdict:
    """Everything the scene needs to draw one object's judged life."""
    obj_kind: str                       # "circle" | "slider" | "spinner"
    kind: JudgmentKind                  # final tier (post-reconcile)
    start_time: float
    end_time: float
    pos: tuple[float, float]            # stacked head position
    popup_pos: tuple[float, float]      # where the judgment sprite shows
    hit_time: float | None = None       # click time (head/circle); None = head missed
    delta: float | None = None          # click −signed timing error
    deadline: float = 0.0               # head window close (miss anchor)
    parts: list[PartOutcome] = field(default_factory=list)
    tracking: list[tuple[float, float]] = field(default_factory=list)
    breaks: list[float] = field(default_factory=list)   # sliderbreak times only
    slider_record: SliderRecord | None = None           # sliders only
    relabeled: bool = False             # reconcile changed the sim's tier
    quality: float = math.inf           # reconcile rank key (band-mapped)

    def tracked_at(self, t: float) -> bool:
        return any(t0 <= t <= t1 for t0, t1 in self.tracking)


@dataclass
class SimResult:
    """Judgment sim output: per-object verdicts + the popup/combo/score
    event stream, plus the honesty metrics (sim-vs-real delta)."""
    verdicts: dict[int, ObjectVerdict]          # id(obj) → verdict
    events: list[JudgmentEvent]
    sim_counts: tuple[int, int, int, int]       # pre-reconcile 300/100/50/miss
    final_counts: tuple[int, int, int, int]
    real_counts: tuple[int, int, int, int] | None
    relabeled: int
    sim_max_combo: int
    final_max_combo: int
    real_max_combo: int | None
    shakes: int
    spinner_count: int
    lazer: bool = False
    classic: bool = False           # OsuModClassic (CL) forced the stable path
    detail_lines: list[str] = field(default_factory=list)
    # part-level combo CHANGES (time, combo) — every increment (circles,
    # spinner ends, slider heads/ticks/repeats/tails) and every reset, so
    # the HUD combo counter steps mid-slider and breaks at the REAL break
    # moment (not the next object judgment). Built alongside events.
    combo_timeline: list[tuple[float, int]] = field(default_factory=list)

    def verdict_for(self, obj) -> ObjectVerdict | None:
        return self.verdicts.get(id(obj))

    def report_lines(self) -> list[str]:
        c = "/".join(str(v) for v in self.sim_counts)
        engine = "lazer" if self.lazer else "stable"
        if self.classic:
            engine += " (Classic mod)"
        lines = []
        if self.real_counts is not None:
            r = "/".join(str(v) for v in self.real_counts)
            lines.append(
                f"ruleset[{engine}]: sim {c} vs replay {r} → "
                f"{self.relabeled} relabeled "
                f"(sim max combo {self.sim_max_combo}, replay {self.real_max_combo}, "
                f"final {self.final_max_combo}; {self.shakes} notelock shakes)")
        else:
            lines.append(f"ruleset[{engine}]: sim {c} (no replay counts to "
                         f"reconcile against; max combo {self.sim_max_combo})")
        if self.spinner_count:
            lines.append(f"ruleset: {self.spinner_count} spinner(s) judged by the "
                         "simplified rotation model (reconcile keeps totals exact)")
        lines.extend(self.detail_lines)
        return lines


@dataclass
class _ObjSim:
    obj: object
    idx: int
    kind: str                    # "circle" | "slider" | "spinner"
    pos: tuple[float, float]
    start: float
    end: float
    deadline: float
    stack_index: int
    head_judged: bool = False
    head_hit: bool = False
    head_result: JudgmentKind | None = None
    hit_time: float | None = None
    delta: float | None = None
    parts: list[PartOutcome] = field(default_factory=list)
    tracking: list[tuple[float, float]] = field(default_factory=list)
    breaks: list[float] = field(default_factory=list)
    final: JudgmentKind = JudgmentKind.MISS


# --- the ruleset -------------------------------------------------------------------

class StdRuleset:
    """Offline judgment simulation over the whole replay (the record loop
    then consumes the precomputed verdicts — same architecture as the
    shipped mania/catch/taiko engines).

    simulate() → reconcile_to_counts() → build_events(), or just run().
    """

    FOLLOW_CIRCLE_RADIUS_MULT = FOLLOW_CIRCLE_RADIUS_MULT
    LAZER_GAME_VERSION = 30_000_000     # .osr game_version threshold

    def __init__(self, beatmap, frames, meta=None, lazer: bool | None = None,
                 reconcile: bool = True):
        self.beatmap = beatmap
        self.frames = frames
        self.meta = meta
        # reconcile=False for a FAILED replay: the .osr's counts are the
        # partial tally AT the death point, not full-map totals, so snapping
        # the whole-map sim to them would be nonsense — the raw sim (frames
        # end at death → post-death objects miss) is what actually happened,
        # and the HUD reads counts_at(fail_time) for the frozen results.
        self.do_reconcile = reconcile
        self.diff = beatmap.diff
        self.hw = OsuHitWindows(self.diff.od)
        self.radius = self.diff.get_radius()
        self.shake_times: list[float] = []
        # Classic mod (CL): a lazer replay explicitly played with the
        # Classic mod must be judged with STABLE semantics. OsuModClassic
        # (osu.Game.Rulesets.Osu/Mods/OsuModClassic.cs) toggles
        # ClassicNoteLock (LegacyHitPolicy) + NoSliderHeadAccuracy
        # (classic aggregate slider judging) on by default — both of
        # which this engine selects via its stable (not-lazer) path.
        # Detected from the .osr ScoreInfo mod list (meta.has_classic_mod).
        self.classic = bool(meta is not None
                            and getattr(meta, "has_classic_mod", False))
        if lazer is None:
            lazer = bool(meta is not None
                         and getattr(meta, "game_version", 0)
                         >= self.LAZER_GAME_VERSION)
            if self.classic:
                lazer = False   # CL overrides the game_version auto-detect
        self.lazer = lazer
        # lazer slider-part judgement counts (LargeTick / SliderTail)
        # from the .osr ScoreInfo — reconciled onto the sim's tick/tail
        # combo outcomes for lazer replays (None → cursor guess stands).
        self.lazer_stats = (getattr(meta, "lazer_statistics", None)
                            if meta is not None else None)
        self.slider_reconcile_note: str | None = None
        self.combo_reconcile_note: str | None = None

    # ---- public API ---------------------------------------------------------------

    def run(self) -> SimResult:
        sims = self.simulate()
        sim_counts = self._tally(sims)
        sim_events, sim_max_combo, sim_timeline = self._build_events(sims)
        relabeled = 0
        real_counts = None
        real_max_combo = None
        if self.meta is not None:
            real_counts = (self.meta.count_300, self.meta.count_100,
                           self.meta.count_50, self.meta.count_miss)
            real_max_combo = self.meta.max_combo
            if self.do_reconcile:
                relabeled = self.reconcile_to_counts(sims, real_counts)
        # lazer-only: reconcile the slider tick/tail COMBO outcomes to the
        # ScoreInfo statistics (the note reconcile above only fixed the
        # 300/100/50/miss tiers). Stable replays carry no ScoreInfo → the
        # cursor-tracking guess stands untouched.
        parts_reconciled = False
        if (self.do_reconcile and self.lazer
                and self.lazer_stats is not None):
            parts_reconciled = self.reconcile_slider_parts(sims, real_max_combo)
        # GENERAL max-combo position pass (all engines): the note reconcile above
        # snaps the 300/100/50/miss COUNTS exactly but places the resulting
        # combo-breaks by cursor-quality rank, so the longest unbroken run (the
        # displayed max combo) drifts from — and sometimes breaks — the replay's
        # real max combo. reconcile_slider_parts only corrects this for lazer
        # replays carrying ScoreInfo tick/tail stats (and only via tick breaks,
        # which a near-FC play has too few of). This generalizes that position
        # pass to EVERY complete play by relocating object-level (circle/spinner,
        # and lazer slider-head) misses — count-preserving — until the longest
        # run equals real_max_combo. No-op when the combo already matches.
        combo_reconciled = False
        if (self.do_reconcile and real_counts is not None
                and real_max_combo is not None and real_max_combo > 0
                # only when the note reconcile actually ran (same guard as
                # reconcile_to_counts): an incomplete/quit play whose counts
                # don't total the object count keeps its RAW sim untouched — the
                # combo pass must not reshape a partial tally either.
                and sum(real_counts) == len(sims)):
            combo_reconciled = self._reconcile_max_combo(sims, real_max_combo)
        final_counts = self._tally(sims)
        events, final_max_combo, timeline = (
            self._build_events(sims)
            if (relabeled or parts_reconciled or combo_reconciled)
            else (sim_events, sim_max_combo, sim_timeline))
        verdicts = {id(s.obj): self._verdict(s) for s in sims}
        return SimResult(
            verdicts=verdicts, events=events,
            sim_counts=sim_counts, final_counts=final_counts,
            real_counts=real_counts, relabeled=relabeled,
            sim_max_combo=sim_max_combo, final_max_combo=final_max_combo,
            real_max_combo=real_max_combo, shakes=len(self.shake_times),
            spinner_count=sum(1 for s in sims if s.kind == "spinner"),
            lazer=self.lazer,
            classic=self.classic,
            detail_lines=self._detail_lines(sims),
            combo_timeline=timeline,
        )

    def _detail_lines(self, sims: list[_ObjSim]) -> list[str]:
        """One distinct log line per miss-kind occurrence: `miss@t` for
        circles/spinners, and the three slider kinds — `head_missed@t`
        (sliderbreak at the head), `sliderbreak@t` (tick/repeat) and
        `tail_missed@t` (lenient, no break)."""
        lines: list[str] = []
        if self.slider_reconcile_note:
            lines.append(self.slider_reconcile_note)
        if self.combo_reconcile_note:
            lines.append(self.combo_reconcile_note)
        for s in sims:
            if s.kind == "slider":
                if not s.head_hit:
                    note = ("miss count" if self.lazer else
                            "combo reset, no miss count")
                    lines.append(f"ruleset: head_missed@{s.deadline:.0f}ms "
                                 f"(slider #{s.idx}, {note})")
                for bt in s.breaks:
                    lines.append(f"ruleset: sliderbreak@{bt:.0f}ms "
                                 f"(slider #{s.idx}, tick/repeat, combo reset)")
                tail = next((p for p in s.parts if p.kind == "tail"), None)
                if tail is not None and not tail.hit:
                    lines.append(f"ruleset: tail_missed@{tail.time:.0f}ms "
                                 f"(slider #{s.idx}, lenient — no combo reset)")
            elif s.final is JudgmentKind.MISS:
                t = s.deadline if s.kind == "circle" else s.end
                lines.append(f"ruleset: miss@{t:.0f}ms ({s.kind} #{s.idx})")
        return lines

    # ---- phase 1: click routing (circles + slider heads) ---------------------------

    def simulate(self) -> list[_ObjSim]:
        sims = self._make_sims()
        clickables = [s for s in sims if s.kind != "spinner"]
        held = HeldState(self.frames)

        first_live = 0
        for press in press_edges(self.frames):
            first_live = self._close_windows(clickables, first_live, press.time_ms)
            self._route_click(sims, clickables, first_live, press)
        self._close_windows(clickables, 0, math.inf)

        for s in sims:
            if s.kind == "slider":
                self._evaluate_slider(s, held)
            elif s.kind == "spinner":
                self._evaluate_spinner(s, held)
            else:
                s.final = s.head_result if s.head_hit else JudgmentKind.MISS
            s.quality = self._quality(s)
        return sims

    def _make_sims(self) -> list[_ObjSim]:
        objs = sorted(self.beatmap.hit_objects, key=lambda o: o.get_start_time())
        sims = []
        for i, obj in enumerate(objs):
            if isinstance(obj, Spinner):
                kind = "spinner"
            elif isinstance(obj, Slider):
                kind = "slider"
            else:
                kind = "circle"
            start = obj.get_start_time()
            sims.append(_ObjSim(
                obj=obj, idx=i, kind=kind,
                pos=obj.get_stacked_start_position(self.diff),
                start=start, end=obj.get_end_time(),
                deadline=start + self.hw.meh,
                stack_index=obj.get_stack_index(self.diff)))
        return sims

    def _close_windows(self, clickables: list[_ObjSim], first_live: int,
                       now: float) -> int:
        """DrawableHitCircle.CheckForResult(!userTriggered): once
        !CanBeHit(timeOffset) — i.e. now past start+meh — the object is
        missed at its window close ('misses unlock at the miss window's
        close', the stable notelock release)."""
        i = first_live
        while i < len(clickables):
            s = clickables[i]
            if s.head_judged:
                if i == first_live:
                    first_live += 1
                i += 1
                continue
            if s.deadline < now:
                s.head_judged = True
                s.head_hit = False
                i += 1
                continue
            break
        return first_live

    def _route_click(self, sims: list[_ObjSim], clickables: list[_ObjSim],
                     first_live: int, press: Press) -> None:
        """One click through stable notelock — LegacyHitPolicy.CheckHittable
        + DrawableHitCircle.CheckForResult(userTriggered)."""
        t = press.time_ms
        scan_open = max(self.diff.preempt, MISS_WINDOW)
        r2 = self.radius * self.radius

        # the click targets the EARLIEST unjudged clickable under the cursor
        # (input walks top-down; older objects draw on top — §5.3)
        target = None
        for s in clickables[first_live:]:
            if s.start - scan_open > t:
                break
            if s.head_judged:
                continue
            dx = press.x - s.pos[0]
            dy = press.y - s.pos[1]
            if dx * dx + dy * dy <= r2:
                target = s
                break
        if target is None:
            return

        delta = t - target.start
        result = self.hw.result_for(delta)

        if self.lazer:
            # StartTimeOrderedHitPolicy (ppy/osu UI/StartTimeOrderedHitPolicy.cs):
            # the LAST circle/head before the target blocks unless it is
            # judged or its start time has passed.
            blocking = None
            for s2 in clickables[first_live:]:
                if s2.start >= target.start:
                    break
                if s2 is target:
                    break
                blocking = s2
            if (blocking is not None and not blocking.head_judged
                    and t < blocking.start):
                self.shake_times.append(t)
                return
            if result is None:
                self.shake_times.append(t)
                return
            # HandleHit: force-miss every earlier unjudged circle/head at
            # the moment of this hit (MissForcefully).
            for s2 in clickables[first_live:]:
                if s2.start >= target.start or s2 is target:
                    break
                if not s2.head_judged:
                    s2.head_judged = True
                    s2.head_hit = False
                    s2.deadline = min(s2.deadline, t)   # miss anchors here
        else:
            # LegacyHitPolicy (stable): a previous alive STACKED object that
            # is unjudged swallows the click entirely (ClickAction.Ignore).
            if target.idx > 0:
                prev = sims[target.idx - 1]
                if prev.stack_index > 0 and not self._is_judged(prev, t):
                    return
            if result is None:                      # outside every window → Shake
                self.shake_times.append(t)
                return
            # any earlier unjudged object whose end is more than 3 ms before
            # this object's start blocks the hit (notelock Shake).
            for s2 in clickables[first_live:]:
                if s2 is target:
                    break
                if s2.head_judged:
                    continue
                if s2.end + NOTELOCK_END_LENIENCY < target.start:
                    self.shake_times.append(t)
                    return
            if abs(delta) >= MISS_WINDOW:           # outside HittableRange
                self.shake_times.append(t)
                return

        target.head_judged = True
        target.head_hit = True
        target.head_result = result
        target.hit_time = t
        target.delta = delta

    def _is_judged(self, s: _ObjSim, now: float) -> bool:
        if s.kind == "spinner":
            return now > s.end
        return s.head_judged

    # ---- phase 2: slider tracking ---------------------------------------------------

    def _evaluate_slider(self, s: _ObjSim, held: HeldState) -> None:
        """SliderInputManager follow-area tracking, stable-flavoured:
        cursor within 1× CircleRadius of the ball to START tracking, 2.4×
        (FOLLOW_AREA) to KEEP it, with any key held. Ticks/repeats score iff
        tracking at their time; the tail is judged at the legacy last tick
        (max(start + duration/2, end − 36) — SliderEventGenerator), displayed
        at endTime. Stable allows tracking without a head hit."""
        obj = s.obj
        start, end = s.start, s.end
        duration = max(end - start, 0.0)
        legacy_last_t = max(start + duration / 2.0, end + TAIL_LENIENCY)

        # scoring events: ticks + repeats at their own times, tail at the
        # legacy last tick time
        judge_pts: list[tuple[float, object]] = []
        for tp in obj.score_points:
            jt = legacy_last_t if tp.kind == "last" else tp.time
            judge_pts.append((jt, tp))
        judge_pts.sort(key=lambda e: e[0])

        # sample lattice: every integer ms across the slide + judge times
        ts = sorted(set(
            [start, end, legacy_last_t]
            + [float(t) for t in range(math.ceil(start), math.floor(end) + 1)]
            + [jt for jt, _ in judge_pts]))

        r_start = self.radius
        r_keep = self.radius * FOLLOW_CIRCLE_RADIUS_MULT
        tracking = False
        t_open: float | None = None
        outcomes: dict[int, bool] = {}
        jp_i = 0
        for t in ts:
            cx, cy, _ = cursor_at(self.frames, t)
            bx, by = obj.get_stacked_position_at(t, self.diff)
            rr = r_keep if tracking else r_start
            in_area = (cx - bx) ** 2 + (cy - by) ** 2 <= rr * rr
            now_tracking = in_area and held.any_at(t)
            if now_tracking and not tracking:
                t_open = t
            elif tracking and not now_tracking and t_open is not None:
                s.tracking.append((t_open, t))
                t_open = None
            tracking = now_tracking
            while jp_i < len(judge_pts) and judge_pts[jp_i][0] <= t:
                outcomes[jp_i] = tracking
                jp_i += 1
        if tracking and t_open is not None:
            s.tracking.append((t_open, end))
        while jp_i < len(judge_pts):                # numeric stragglers
            outcomes[jp_i] = tracking
            jp_i += 1

        # part list: head + ticks/repeats + tail (display times, not judge
        # times). The three miss kinds (owner-specified stable model):
        # head miss = sliderbreak at the head's window close; tick/REPEAT
        # miss = the classic sliderbreak (repeats tick-equivalent); tail
        # miss = lenient (no break, aggregate downgrade only).
        s.parts = [PartOutcome(time=start, kind="head", pos=s.pos, hit=s.head_hit)]
        for i, (jt, tp) in enumerate(judge_pts):
            hit = outcomes.get(i, False)
            kind = {"tick": "tick", "reverse": "repeat", "last": "tail"}[tp.kind]
            pos = obj.modify_position(tp.pos, self.diff)
            disp_t = end if tp.kind == "last" else tp.time
            # cursor-to-ball distance at the JUDGE time — the tracking-quality
            # key the lazer tick/tail reconcile ranks by (reconcile_slider_parts)
            cxj, cyj, _ = cursor_at(self.frames, jt)
            bxj, byj = obj.get_stacked_position_at(jt, self.diff)
            mgn = math.hypot(cxj - bxj, cyj - byj)
            s.parts.append(PartOutcome(time=disp_t, kind=kind, pos=pos,
                                       hit=hit, margin=mgn))
            if not hit and kind != "tail":
                s.breaks.append(tp.time)            # tick/repeat miss = sliderbreak

        if self.lazer:
            # lazer counts the HEAD's timing judgment (a missed head is a
            # Miss count); the classic aggregate is stable-only.
            s.final = (s.head_result if s.head_hit else JudgmentKind.MISS)
        else:
            s.final = _classic_slider_aggregate(s.parts)

    # ---- phase 3: spinners (simplified) ----------------------------------------------

    def _evaluate_spinner(self, s: _ObjSim, held: HeldState) -> None:
        """SIMPLIFIED spinner model: rotation accumulated from per-frame
        cursor angle deltas around the playfield centre while a key is held;
        required spins = duration_s × SpinnerRatio (§2.4). Result tiers per
        DrawableSpinner.CheckForResult (progress ≥1 → Great, >0.9 → Ok,
        >0.75 → Meh, else Miss). No per-spin bonus."""
        cx, cy = 256.0, 192.0
        total = 0.0
        prev_angle: float | None = None
        prev_held = False
        for f in self.frames:
            if f.time_ms < s.start:
                continue
            if f.time_ms > s.end:
                break
            ang = math.atan2(f.y - cy, f.x - cx)
            is_held = bool(f.keys & (_CH1 | _CH2))
            if prev_angle is not None and is_held and prev_held:
                d = math.atan2(math.sin(ang - prev_angle),
                               math.cos(ang - prev_angle))
                total += abs(d)
            prev_angle = ang
            prev_held = is_held
        rotations = total / (2.0 * math.pi)
        # osu! SpinsRequired is an INTEGER count of full spins — both engines
        # truncate it (Spinner.ApplyDefaultsToSelf / stable's rotation
        # requirement are `(int)(...)`), NOT the continuous fraction the
        # progress meter draws. A spinner too short to require even one full
        # spin (SpinsRequired == 0) is already complete: Progress → 1 → Great,
        # no spin needed. Aspire micro-spinners (e.g. Time Traveler's 23 ms
        # centre spinners) live here — the old un-truncated fractional
        # requirement demanded a fraction of a spin the player never made in
        # ~1 frame and wrongly MISSED every one of them. Truncation only ever
        # lowers the requirement, so it cannot manufacture a new over-miss.
        seconds_duration = (s.end - s.start) / 1000.0
        spins_required = int(seconds_duration * self.diff.spinner_ratio)
        progress = 1.0 if spins_required <= 0 else rotations / spins_required
        if progress >= 1.0:
            s.final = JudgmentKind.HIT300
        elif progress > 0.9:
            s.final = JudgmentKind.HIT100
        elif progress > 0.75:
            s.final = JudgmentKind.HIT50
        else:
            s.final = JudgmentKind.MISS
        s.head_judged = True
        s.head_hit = s.final is not JudgmentKind.MISS
        s.hit_time = s.end if s.head_hit else None
        s.delta = 0.0 if s.head_hit else None

    # ---- reconcile (mania v2 judgments.py pattern) ------------------------------------

    def _quality(self, s: _ObjSim) -> float:
        """Band-mapped rank key: every object maps into the window band of
        its simmed tier, so a rank-order reassignment (a) is a no-op when
        the sim already matches the replay's counts and (b) flips the
        objects nearest a band boundary first when it doesn't."""
        hw = self.hw
        if s.final is JudgmentKind.MISS:
            return math.inf
        if s.kind == "circle" or (s.kind == "slider" and self.lazer):
            # lazer slider heads are timing judgments — rank like circles
            return abs(s.delta) if s.delta is not None else hw.great * 0.5
        if s.kind == "spinner":
            mid = {JudgmentKind.HIT300: 1.0,
                   JudgmentKind.HIT100: (hw.great + hw.ok) / 2.0,
                   JudgmentKind.HIT50: (hw.ok + hw.meh) / 2.0}
            return mid[s.final]
        # slider: place inside the band by hit fraction (versus_telemetry's
        # "miss the LAST ticks" ordering philosophy)
        n = len(s.parts)
        frac = (sum(1 for p in s.parts if p.hit) / n) if n else 0.0
        if s.final is JudgmentKind.HIT300:
            d = abs(s.delta) if (s.head_hit and s.delta is not None) else hw.great * 0.6
            return min(d, hw.great - 0.01)
        if s.final is JudgmentKind.HIT100:            # frac ∈ [0.5, 1)
            w = min(max((1.0 - frac) * 2.0, 0.0), 1.0)
            return hw.great + 0.01 + w * (hw.ok - hw.great - 0.02)
        w = min(max((0.5 - frac) * 2.0, 0.0), 1.0)     # HIT50, frac ∈ (0, 0.5)
        return hw.ok + 0.01 + w * (hw.meh - hw.ok - 0.02)

    def reconcile_to_counts(self, sims: list[_ObjSim],
                            real_counts: tuple[int, int, int, int]) -> int:
        """Snap the sim's final tallies to the replay's authoritative
        300/100/50/miss counts (adapted from OsuManiaRenderer_v2
        judgments.reconcile_to_counts): rank every object by band-mapped
        quality (misses last, tie-broken by time) and hand out the recorded
        number of each tier in order. Exact-match sims relabel nothing."""
        n300, n100, n50, nmiss = real_counts
        if n300 + n100 + n50 + nmiss != len(sims):
            print(f"WARNING: replay counts total {n300 + n100 + n50 + nmiss} "
                  f"!= {len(sims)} objects — reconcile skipped", file=sys.stderr)
            return 0
        order = sorted(range(len(sims)),
                       key=lambda i: (sims[i].quality, sims[i].start))
        tiers = ([JudgmentKind.HIT300] * n300 + [JudgmentKind.HIT100] * n100
                 + [JudgmentKind.HIT50] * n50 + [JudgmentKind.MISS] * nmiss)
        relabeled = 0
        for rank, i in enumerate(order):
            s = sims[i]
            want = tiers[rank]
            if want is s.final:
                continue
            relabeled += 1
            self._relabel(s, want)
        return relabeled

    def _relabel(self, s: _ObjSim, want: JudgmentKind) -> None:
        s.final = want
        if s.kind == "slider" and not self.lazer:
            self._repair_slider_parts(s, want)
            return
        if s.kind == "slider":
            # lazer: the tier IS the head's timing judgment — keep parts,
            # fix the head's hit/miss state to match the new tier
            head = s.parts[0] if s.parts else None
            if want is JudgmentKind.MISS:
                s.head_hit = False
                s.hit_time = None
                s.delta = None
                if head is not None:
                    head.hit = False
            elif s.hit_time is None:
                s.head_hit = True
                s.hit_time = s.start
                s.delta = 0.0
                if head is not None:
                    head.hit = True
            return
        # circle / spinner
        if want is JudgmentKind.MISS:
            s.head_hit = False
            s.hit_time = None
            s.delta = None
        elif s.hit_time is None:
            # phantom hit (sim missed, replay says hit): land it on time so
            # downstream visuals still resolve — mania's 0 ms-offset trick
            s.head_hit = True
            s.hit_time = s.start
            s.delta = 0.0
            s.breaks.clear()

    def _repair_slider_parts(self, s: _ObjSim, want: JudgmentKind) -> None:
        """Nudge the part outcomes into the band of the relabeled tier so
        tick/tail visuals stay coherent with the popup: flip the LAST parts
        (versus_telemetry's tick-budget convention), minimally."""
        n = len(s.parts)
        if n == 0:
            return
        cur = sum(1 for p in s.parts if p.hit)
        lo_hi = {JudgmentKind.HIT300: (n, n),
                 JudgmentKind.MISS: (0, 0),
                 JudgmentKind.HIT100: (math.ceil(n / 2.0), max(n - 1, 1)),
                 JudgmentKind.HIT50: (1, max(math.ceil(n / 2.0) - 1, 1))}
        lo, hi = lo_hi[want]
        target = min(max(cur, lo), hi)
        if target > cur:                        # un-miss the latest misses
            for p in reversed(s.parts):
                if cur >= target:
                    break
                if not p.hit:
                    p.hit = True
                    cur += 1
        elif target < cur:                      # miss the LAST hits
            for p in reversed(s.parts):
                if cur <= target:
                    break
                if p.hit:
                    p.hit = False
                    cur -= 1
        if s.parts[0].hit and s.hit_time is None:   # phantom head hit
            s.head_hit = True
            s.hit_time = s.start
            s.delta = 0.0
        if not s.parts[0].hit:
            s.head_hit = False
            s.hit_time = None
            s.delta = None
        # rebuild the sliderbreak list (tick/repeat misses) from the
        # repaired parts; the head break is carried by head_missed_at
        s.breaks = [p.time for p in s.parts[1:]
                    if not p.hit and p.kind != "tail"]

    # ---- lazer slider-part reconcile (tick/tail combo faithfulness) --------------------

    def reconcile_slider_parts(self, sims: list[_ObjSim],
                               real_max_combo: int | None) -> bool:
        """LAZER-only: snap the sim's slider tick/repeat and tail COMBO
        outcomes to the .osr ScoreInfo statistics, then reshape which parts
        are misses so the longest unbroken combo run equals the replay's real
        max combo. Returns True if it ran (a part may or may not have flipped).

        COMBO MODEL (ppy/osu master — HitResult.cs `AffectsCombo/IsHit`,
        Judgement.cs default `MinResult`, SliderTick/SliderRepeat/
        SliderTailCircle):
          * SliderTick / SliderRepeat  -> HitResult.LargeTickHit on hit
            (AffectsCombo && IsHit  => IncreasesCombo, +1), default MinResult
            HitResult.LargeTickMiss on miss (AffectsCombo && !IsHit =>
            BreaksCombo, reset to 0).
          * SliderTailCircle (non-classic TailJudgement) -> HitResult
            .SliderTailHit on hit (AffectsCombo && IsHit => IncreasesCombo,
            +1); its default MinResult is HitResult.IgnoreMiss on miss
            (AffectsCombo == false => NO combo effect — a missed tail neither
            breaks nor increments). So the ONLY combo-breaking slider parts
            are tick/repeat misses.
        The note tiers (300/100/50/miss = circles + slider HEADS) are already
        snapped by reconcile_to_counts; this pass never touches heads.

        WHICH parts become misses is chosen by per-part cursor tracking quality
        (PartOutcome.margin): worst-tracked ticks -> the LargeTickMisses,
        best-tracked tails -> the SliderTailHits. The exact break POSITIONS are
        NOT in the replay (only the aggregate counts are), so after the
        count-snap a minimal position pass shifts placement WITHIN the
        reconciled counts until the longest run equals real_max_combo. This is
        the honest approximation: counts exact, positions cursor-ranked.
        """
        stats = self.lazer_stats
        if stats is None:
            return False

        ticks: list[PartOutcome] = []
        tails: list[PartOutcome] = []
        for s in sims:
            if s.kind != "slider":
                continue
            for p in s.parts:
                if p.kind in ("tick", "repeat"):
                    ticks.append(p)
                elif p.kind == "tail":
                    tails.append(p)
        if not ticks and not tails:
            return False

        want_tail_hit = max(0, min(stats.slider_tail_hit, len(tails)))
        want_tick_miss = max(0, min(stats.large_tick_miss, len(ticks)))
        T = real_max_combo if (real_max_combo and real_max_combo > 0) else None

        # (1) tails: best-tracked (smallest margin) become the SliderTailHits.
        tails.sort(key=lambda p: p.margin)
        for i, p in enumerate(tails):
            p.hit = i < want_tail_hit

        # combo-affecting event lattice (mirrors _lattice's combo subset, in
        # the same object/part order so a stable time-sort matches _build_events
        # exactly): "hit"/"brk" = FIXED increment/break (circles, spinners,
        # slider heads — set by the note reconcile); "tick" = MOVABLE (hit=+1,
        # miss=break); "tail" = MOVABLE (hit=+1, miss=inert).
        def build_events():
            ev = []
            for s in sims:
                if s.kind == "slider":
                    for p in s.parts:
                        if p.kind == "head":
                            t = (s.hit_time if (p.hit and s.hit_time is not None)
                                 else (s.deadline if not p.hit else p.time))
                            ev.append((t, "hit" if p.hit else "brk", None))
                        elif p.kind == "tail":
                            ev.append((p.time, "tail", p))
                        else:
                            ev.append((p.time, "tick", p))
                else:
                    t = (s.hit_time if s.hit_time is not None
                         else (s.deadline if s.kind == "circle" else s.end))
                    ev.append((t, "hit" if s.final is not JudgmentKind.MISS
                               else "brk", None))
            ev.sort(key=lambda e: e[0])
            return ev

        events = build_events()
        tick_break: set[int] = set()

        def segments():
            """Runs delimited by fixed breaks + the chosen tick-breaks, each
            (length, [(incs_before, tick_part)...], [tail_part...])."""
            segs = []
            st: list = []
            sta: list = []
            ln = 0
            for (t, ty, ref) in events:
                is_break = (ty == "brk"
                            or (ty == "tick" and id(ref) in tick_break))
                if is_break:
                    segs.append((ln, st, sta))
                    ln = 0
                    st = []
                    sta = []
                elif ty == "hit":
                    ln += 1
                elif ty == "tick":
                    st.append((ln, ref))
                    ln += 1
                else:                       # tail
                    sta.append(ref)
                    if ref.hit:
                        ln += 1
            segs.append((ln, st, sta))
            return segs

        # (2) place exactly want_tick_miss LargeTickMisses: split the longest
        # run at the tick nearest (but not past) the target so a run lands on
        # T; once every run <= T, spend the remaining budget on the
        # worst-tracked ticks OUTSIDE the peak run(s) (quality preference).
        for _ in range(want_tick_miss):
            segs = segments()
            ln, st, _sta = max(segs, key=lambda seg: seg[0])
            gmax = ln
            if T is not None and ln > T and st:
                le = [x for x in st if x[0] <= T]
                pick = (max(le, key=lambda x: x[0]) if le
                        else min(st, key=lambda x: x[0]))
                tick_break.add(id(pick[1]))
            else:
                protect = set(id(rp) for (rl, rst, _rsa) in segs if rl == gmax
                              for (_ib, rp) in rst)
                pool = [p for p in ticks
                        if id(p) not in tick_break and id(p) not in protect]
                if not pool:
                    pool = [p for p in ticks if id(p) not in tick_break]
                if not pool:
                    break
                pool.sort(key=lambda p: -p.margin)
                tick_break.add(id(pool[0]))

        for p in ticks:
            p.hit = id(p) not in tick_break

        # (3) tail fine-adjust: nudge the peak run to EXACTLY T while holding
        # the tail-hit count — swap best-tracked interior misses for
        # worst-tracked exterior hits (or the reverse to shrink). Tails never
        # break combo, so this only changes run LENGTHS, never break positions.
        if T is not None:
            segs = segments()
            mi = max(range(len(segs)), key=lambda k: segs[k][0])
            m_len = segs[mi][0]
            if m_len != T:
                peak_tails = segs[mi][2]
                other_tails = [p for k in range(len(segs)) if k != mi
                               for p in segs[k][2]]
                if m_len < T:
                    promote = sorted((p for p in peak_tails if not p.hit),
                                     key=lambda p: p.margin)
                    demote = sorted((p for p in other_tails if p.hit),
                                    key=lambda p: -p.margin)
                    k = min(T - m_len, len(promote), len(demote))
                    for j in range(k):
                        promote[j].hit = True
                        demote[j].hit = False
                else:
                    demote = sorted((p for p in peak_tails if p.hit),
                                    key=lambda p: -p.margin)
                    promote = sorted((p for p in other_tails if not p.hit),
                                     key=lambda p: p.margin)
                    k = min(m_len - T, len(demote), len(promote))
                    for j in range(k):
                        demote[j].hit = False
                        promote[j].hit = True

        # rebuild every slider's sliderbreak list from the reconciled parts.
        for s in sims:
            if s.kind == "slider":
                s.breaks = [p.time for p in s.parts[1:]
                            if not p.hit and p.kind != "tail"]

        tick_miss_final = sum(1 for p in ticks if not p.hit)
        tail_hit_final = sum(1 for p in tails if p.hit)
        self.slider_reconcile_note = (
            "ruleset[lazer]: slider parts reconciled to ScoreInfo — "
            f"large-tick miss {tick_miss_final} (replay {stats.large_tick_miss}), "
            f"slider-tail hit {tail_hit_final} (replay {stats.slider_tail_hit}); "
            "break positions cursor-quality-ranked")
        return True

    # ---- general max-combo position pass (all engines) ---------------------------------

    def _reconcile_max_combo(self, sims: list[_ObjSim],
                             real_max_combo: int) -> bool:
        """Count-preserving position pass that relocates OBJECT-level misses so
        the longest unbroken combo run equals the replay's real max combo.

        Generalizes reconcile_slider_parts' combo shaping — which only moves
        slider tick/tail breaks, and only for lazer replays carrying ScoreInfo —
        to EVERY complete play. After reconcile_to_counts has fixed the exact
        300/100/50/miss totals, WHICH objects carry the miss is only ranked by
        cursor quality, so the longest run (the displayed max combo) drifts from,
        and sometimes BREAKS, the replay's real max combo even though every count
        is perfect. Holding all slider ticks/tails (already reconciled) and — for
        stable — slider heads FIXED, we re-choose which movable objects are the
        misses (same count) to land the longest run on T, preferring the
        worst-tracked objects as the misses where position is free.

        MOVABLE = circles + spinners (both engines) + slider HEADS on the lazer
        path only (a lazer slider's tick/tail results are scored independently of
        its head, so toggling the head never disturbs the tick/tail counts that
        reconcile_slider_parts snapped to ScoreInfo; a stable slider's parts are
        coupled to its aggregate tier, so stable heads stay fixed).

        Counts stay EXACT — the movable objects' tier multiset is preserved; only
        WHICH object holds each tier changes. Break POSITIONS remain a
        cursor-quality-ranked approximation (the replay carries no per-object
        combo truth), but the headline max-combo NUMBER now matches. No-op when
        the combo already equals T, so an already-correct trajectory (including
        the lazer-with-stats path and a clean FC) is never disturbed."""
        T = real_max_combo
        if T <= 0:
            return False

        # authoritative current max combo (same lattice the result reports) —
        # never touch a trajectory that already matches.
        _ev, cur_combo, _tl = self._build_events(sims)
        if cur_combo == T:
            return False

        lazer_slider_movable = self.lazer

        def movable(s: _ObjSim) -> bool:
            return (s.kind in ("circle", "spinner")
                    or (lazer_slider_movable and s.kind == "slider"))

        movables = [s for s in sims if movable(s)]
        if not movables:
            return False

        # combo-affecting event lattice, mirroring _lattice's combo subset.
        # FIX_BRK = fixed break (stable slider head miss, tick/repeat miss);
        # FIX_INC = fixed +1 (fixed slider head hit, tick hit, tail hit); MOVE =
        # one movable object (break if chosen a miss, else +1). Slider aggregate
        # + inert tail miss contribute nothing → omitted. CRITICAL: a MISS's
        # combo effect fires at its window CLOSE (deadline / spinner end — the
        # judgment-time _lattice uses), NOT its start, so a hit that lands inside
        # a miss's window is counted in the run BEFORE the break. The movable
        # event time therefore depends on whether it is a chosen break, so the
        # ordering is rebuilt per candidate set. To match _build_events EXACTLY
        # (including tie-breaks), the emitters are built in _lattice's own
        # per-sim / per-part order and the per-candidate sort is Python's STABLE
        # sort, so equal-time events resolve identically to the real lattice.
        FIX_BRK, FIX_INC, MOVE = 0, 1, 2
        emitters: list[tuple] = []      # ('fix', time, ty) | ('move', sim)
        for s in sims:
            if s.kind == "slider":
                for p in s.parts:
                    if p.kind == "head":
                        if movable(s):
                            emitters.append(("move", s))
                        elif p.hit:
                            t = s.hit_time if s.hit_time is not None else p.time
                            emitters.append(("fix", t, FIX_INC))
                        else:
                            emitters.append(("fix", s.deadline, FIX_BRK))
                    elif p.kind == "tail":
                        if p.hit:                       # tail miss is inert
                            emitters.append(("fix", p.time, FIX_INC))
                    else:                               # tick / repeat
                        emitters.append(("fix", p.time,
                                         FIX_INC if p.hit else FIX_BRK))
            else:                                       # circle / spinner
                emitters.append(("move", s))

        def _move_time(s: _ObjSim, is_break: bool) -> float:
            if is_break:                                # miss registers at close
                return s.end if s.kind == "spinner" else s.deadline
            return s.hit_time if s.hit_time is not None else s.start

        def ordered(chosen: set[int]):
            ev = []
            for e in emitters:
                if e[0] == "fix":
                    ev.append((e[1], e[2], None))
                else:
                    s = e[1]
                    brk = s.idx in chosen
                    ev.append((_move_time(s, brk),
                               FIX_BRK if brk else MOVE, s))
            ev.sort(key=lambda x: x[0])                 # stable → ties match _lattice
            return ev

        # miss budget among movable objects (preserved exactly). With no movable
        # misses there is no combo-break to relocate (adding one would break the
        # count invariant), so the pass cannot help — e.g. a play whose only
        # combo break is a stable slider tick, which this object-miss pass does
        # not own. Leave it untouched rather than emit a no-op note.
        B = sum(1 for s in movables if s.final is JudgmentKind.MISS)
        if B == 0:
            return False

        def segments(chosen: set[int]):
            """Runs delimited by fixed breaks + chosen movable breaks. Each run
            = (length, [(incs_before, movable_sim) ...] of its unchosen movable
            split points)."""
            segs = []
            run = 0
            slots: list = []
            for _t, ty, ref in ordered(chosen):
                if ty == FIX_BRK:
                    segs.append((run, slots))
                    run = 0
                    slots = []
                else:                                   # MOVE (unchosen) or INC
                    if ty == MOVE:
                        slots.append((run, ref))
                    run += 1
            segs.append((run, slots))
            return segs

        # place exactly B movable breaks: split the longest run at the movable
        # slot nearest (but not past) T so a run lands on T; once every run <= T,
        # spend the remaining budget on the worst-tracked movable OUTSIDE the
        # peak run(s) (quality preference — never lowering the peak below T).
        chosen: set[int] = set()
        for _ in range(B):
            segs = segments(chosen)
            ln, slots = max(segs, key=lambda sg: sg[0])
            if ln > T and slots:
                le = [x for x in slots if x[0] <= T]
                pick = (max(le, key=lambda x: x[0]) if le
                        else min(slots, key=lambda x: x[0]))
                chosen.add(pick[1].idx)
            else:
                peak = ln
                protect = {rp.idx for (rl, rs) in segs if rl == peak
                           for (_ib, rp) in rs}
                pool = [s for s in movables
                        if s.idx not in chosen and s.idx not in protect]
                if not pool:
                    pool = [s for s in movables if s.idx not in chosen]
                if not pool:
                    break
                pool.sort(key=lambda s: -s.quality)     # worst-tracked first
                chosen.add(pool[0].idx)

        # exact-target correction: the greedy lands the peak within ~1 of T, but
        # a FIXED slider break sitting inside the peak run can leave it a hair
        # short (no movable slot falls exactly on T). A bounded first-improvement
        # local search over single-break relocations — scored on the exact ordered
        # lattice (identical tie-breaks to _build_events) — nails T whenever one
        # move can, so an already-correct raw combo is restored EXACTLY, not left
        # off by one. Monotone-safe (only ever accepts a strictly-closer
        # arrangement) and hard-capped so it can never dominate a run.
        def peak_len(sel: set[int]) -> int:
            run = mx = 0
            for _t, ty, _ref in ordered(sel):
                if ty == FIX_BRK:
                    run = 0
                else:
                    run += 1
                    if run > mx:
                        mx = run
            return mx

        best_dist = abs(peak_len(chosen) - T)
        # cap total trial evaluations so this can never dominate a render; sized
        # to let the exhaustive single-move scan complete for realistic complete
        # plays (miss-count × free-slot pairs), only a handful of which ever
        # reach this correction at all.
        budget = 40000
        while best_dist and budget > 0:
            # deterministic order (stable .idx, never id()) so the search is
            # reproducible run-to-run and matches the renderer exactly.
            free = [s.idx for s in movables if s.idx not in chosen]
            improved = False
            for c in sorted(chosen):
                for d in free:
                    budget -= 1
                    if budget <= 0:
                        break
                    trial = set(chosen)
                    trial.discard(c)
                    trial.add(d)
                    dist = abs(peak_len(trial) - T)
                    if dist < best_dist:
                        chosen, best_dist, improved = trial, dist, True
                        break
                if improved or budget <= 0:
                    break
            if not improved:
                break

        # apply: preserve the movable tier multiset — the B misses go to the
        # chosen slots, the non-miss tiers are re-handed to the remaining movable
        # objects best-quality-first (reconcile_to_counts' ordering philosophy).
        m300 = sum(1 for s in movables if s.final is JudgmentKind.HIT300)
        m100 = sum(1 for s in movables if s.final is JudgmentKind.HIT100)
        m50 = sum(1 for s in movables if s.final is JudgmentKind.HIT50)
        non_miss = sorted((s for s in movables if s.idx not in chosen),
                          key=lambda s: (s.quality, s.start))
        want = ([JudgmentKind.HIT300] * m300 + [JudgmentKind.HIT100] * m100
                + [JudgmentKind.HIT50] * m50)
        for s, w in zip(non_miss, want):
            self._relabel(s, w)
        for s in movables:
            if s.idx in chosen:
                self._relabel(s, JudgmentKind.MISS)

        self.combo_reconcile_note = (
            "ruleset: max-combo position pass — relocated object misses so the "
            f"longest run targets replay max combo {T} (was {cur_combo}); "
            "break positions cursor-quality-ranked, counts unchanged")
        return True

    # ---- events: popups + combo/acc/score timeline -------------------------------------

    def _tally(self, sims: list[_ObjSim]) -> tuple[int, int, int, int]:
        c = {k: 0 for k in (JudgmentKind.HIT300, JudgmentKind.HIT100,
                            JudgmentKind.HIT50, JudgmentKind.MISS)}
        for s in sims:
            c[s.final] += 1
        return (c[JudgmentKind.HIT300], c[JudgmentKind.HIT100],
                c[JudgmentKind.HIT50], c[JudgmentKind.MISS])

    def _lattice(self, sims: list[_ObjSim]):
        """(time, kind, is_object, sim, base, base_max, combo_mode) events.
        combo_mode: 'inc' = +1 on hit / RESET on miss (circles, spinners,
        slider heads, ticks and repeats — head/tick/repeat misses are all
        sliderbreaks; the head's reset lands at its miss moment, the
        window close); 'inc_noreset' = +1 on hit, no reset on miss (the
        slider TAIL — stable's only lenient part); 'none' = no combo
        contribution (the slider's aggregate judgment — stable combo
        comes from the parts)."""
        ev = []
        for s in sims:
            if s.kind == "slider":
                for p in s.parts:
                    mode = "inc_noreset" if p.kind == "tail" else "inc"
                    # the head's combo effect fires at its judgment moment
                    t = p.time
                    if p.kind == "head":
                        if not p.hit:
                            t = s.deadline
                        elif s.hit_time is not None:
                            t = s.hit_time
                    ev.append((t, s.final, False, s,
                               BASE_LARGE_TICK if p.hit else 0,
                               BASE_LARGE_TICK, mode, p.hit))
                # the slider's counted judgment: stable = classic aggregate
                # at the slider end; lazer = the head's timing judgment at
                # its own moment (that's where the popup shows)
                if self.lazer:
                    obj_t = (s.hit_time if s.hit_time is not None
                             else s.deadline)
                else:
                    obj_t = s.end
                ev.append((obj_t, s.final, True, s,
                           BASE_SCORE[s.final.value], 300, "none",
                           s.final is not JudgmentKind.MISS))
            else:
                t = s.hit_time if s.hit_time is not None else \
                    (s.deadline if s.kind == "circle" else s.end)
                ev.append((t, s.final, True, s,
                           BASE_SCORE[s.final.value], 300, "inc",
                           s.final is not JudgmentKind.MISS))
        ev.sort(key=lambda e: e[0])
        return ev

    def _build_events(
            self, sims: list[_ObjSim],
    ) -> tuple[list[JudgmentEvent], int, list[tuple[float, int]]]:
        """Combo (stable semantics), accuracy (standard std formula over
        object judgments) and lazer-standardised score over the full event
        lattice; emits one JudgmentEvent per OBJECT judgment (the popups).
        Returns (events, max_combo, combo_timeline) — max combo is tracked
        on the lattice so peaks inside a broken slider still count, and the
        timeline records every part-level combo CHANGE (the HUD's counter)."""
        lattice = self._lattice(sims)

        # perfect-run combo portion (denominator of the combo term)
        max_combo_portion = 0.0
        max_base_total = 0.0
        c = 0
        for _t, _k, _is_obj, _s, _b, bmax, mode, _hit in lattice:
            max_base_total += bmax
            if mode == "none":
                continue
            c += 1
            max_combo_portion += bmax * (c ** COMBO_EXPONENT)

        events: list[JudgmentEvent] = []
        timeline: list[tuple[float, int]] = []
        combo = 0
        max_combo = 0
        combo_portion = 0.0
        cur_base = cur_max_base = 0.0
        obj_base = 0.0
        obj_n = 0
        for t, kind, is_obj, s, b, bmax, mode, hit in lattice:
            cur_base += b
            cur_max_base += bmax
            if mode != "none":
                if hit:
                    combo += 1
                    max_combo = max(max_combo, combo)
                    combo_portion += bmax * (combo ** COMBO_EXPONENT)
                    timeline.append((t, combo))
                elif mode == "inc":
                    if combo:
                        timeline.append((t, 0))
                    combo = 0
            if not is_obj:
                continue
            obj_n += 1
            obj_base += BASE_SCORE[kind.value]
            acc = obj_base / (300.0 * obj_n)
            if max_combo_portion > 0 and max_base_total > 0:
                score = (500_000.0 * acc * (combo_portion / max_combo_portion)
                         + 500_000.0 * (acc ** 5) * (cur_max_base / max_base_total))
            else:
                score = 0.0
            px, py = self._popup_pos(s)
            events.append(JudgmentEvent(
                time_ms=t, kind=kind, object_id=s.idx, x=px, y=py,
                combo_after=combo, score_after=int(round(score)),
                acc_after=acc))
        return events, max_combo, timeline

    def _popup_pos(self, s: _ObjSim) -> tuple[float, float]:
        if s.kind == "slider":
            if self.lazer:
                return s.pos            # lazer: the head judgment pops there
            return s.obj.get_stacked_position_at(s.end, self.diff)
        if s.kind == "spinner":
            return (256.0, 192.0)
        return s.pos

    def _verdict(self, s: _ObjSim) -> ObjectVerdict:
        record = None
        if s.kind == "slider" and s.parts:
            record = SliderRecord.from_parts(s.parts, s.delta, s.deadline)
        return ObjectVerdict(
            obj_kind=s.kind, kind=s.final, start_time=s.start, end_time=s.end,
            pos=s.pos, popup_pos=self._popup_pos(s), hit_time=s.hit_time,
            delta=s.delta, deadline=s.deadline, parts=s.parts,
            tracking=s.tracking, breaks=s.breaks, slider_record=record,
            quality=s.quality)


def _classic_slider_aggregate(parts: list[PartOutcome]) -> JudgmentKind:
    """DrawableSlider.CheckForResult, ClassicSliderBehaviour branch
    (ppy/osu DrawableSlider.cs): judged proportionally to nested objects
    hit — all → Great, none → Miss, ≥half → Ok, else Meh."""
    total = len(parts)
    hit = sum(1 for p in parts if p.hit)
    if total == 0 or hit == total:
        return JudgmentKind.HIT300
    if hit == 0:
        return JudgmentKind.MISS
    return JudgmentKind.HIT100 if hit / total >= 0.5 else JudgmentKind.HIT50


# deprecated alias (the Phase-1 stub's name)
Ruleset = StdRuleset
