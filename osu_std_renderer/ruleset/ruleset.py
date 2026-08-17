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
    osu.Game.Rulesets.Osu/Objects/Spinner.cs                   lazer SpinsRequired (CLEAR_RPM_RANGE 90/150/225, duration_error)
    osu.Game.Rulesets.Osu/Objects/Drawables/DrawableSpinner.cs     lazer spinner result tiers (Progress ≥1/>0.9/>0.75)

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
  * spinners: BOTH scoring models are ported, selected by the same
    stable-vs-lazer engine switch as notelock (the old shared model
    compared FULL rotations against stable's HALF-spin ratio — every
    spinner demanded ~2× the real requirement and popped bogus 50s):
      - STABLE (danser-go's replay-verified port of the stable decompile):
        requirement = int(duration_s × DifficultyRate(od, 3, 5, 7.5)) in
        HALF-spins; rotation runs through stable's velocity model (the
        cursor angle sets a theoretical velocity, the actual velocity
        chases it under an acceleration cap and a hard ±0.05 rad/ms cap
        = 477 rpm, physics in REAL time under rate mods) and scores one
        scoringRotation per completed half-spin. Tiers (the 20190510.2
        scoring change, keyed on .osr game_version): ≥req+1 → 300,
        ≥req−1 → 100, ≥req//4 → 50, else miss (req 0 → 300); pre-2019
        replays keep the old ≥req+2 / ≥req+1 / ≥req tiers.
      - LAZER (ppy/osu master): SpinsRequired = int(minRps·duration_s +
        0.0001), minRps = DifficultyRange(od, 90, 150, 225)/60 — FULL
        spins; per-spin max-|rotation| tracking (reversals don't stack),
        deltas ×clock-rate; Progress ≥1 → 300, >0.9 → 100, >0.75 → 50.
    No spin-bonus score events. Rate-RAMP (WU/WD) replays use their base
    rate for spinner physics — noted approximation, reconcile absorbs.
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
import dataclasses
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

# spinners — stable's velocity physics (decompile-derived constants, identical
# in danser-go's replay-verified stable port) + lazer's Spinner.cs values
SPINNER_CENTRE = (256.0, 192.0)     # stable pins spinners to the centre
SPINNER_FRAME_TIME = 1000.0 / 60.0  # stable's nominal frame cadence (ms)
SPINNER_VEL_CAP = 0.05              # rad/ms hard velocity cap (= 477 rpm)
SPINNER_AUTO_VEL = 0.03             # rad/ms SpunOut/Autopilot constant spin
LAZER_SPIN_DURATION_ERROR = 0.0001  # Spinner.ApplyDefaultsToSelf duration_error
NEW_SPINNER_SCORING_VERSION = 20190510  # stable build that re-tiered spinners
                                        # (cuttingedge/20190510.2 changelog)

# lazer ScoreProcessor.GetBaseScoreForResult (Great/Ok/Meh/LargeTickHit)
BASE_SCORE = {"300": 300, "100": 100, "50": 50, "miss": 0}
BASE_LARGE_TICK = 30
LAZER_TAIL_ACC = 150               # HitResult.SliderTailHit numeric — the
                                   # slider TAIL's weight in lazer's
                                   # accuracy (a missed tail is IgnoreMiss,
                                   # 0 of 150 — the denominator still grows)
COMBO_EXPONENT = 0.5                # ScoreProcessor.COMBO_EXPONENT
SMALL_BONUS_SCORE = 10             # Judgement.SMALL_BONUS_SCORE (bonus portion)
LARGE_BONUS_SCORE = 50             # Judgement.LARGE_BONUS_SCORE (bonus portion)

# osu!lazer ScoreV3 total-score mod multipliers — UPDATED per ppy/osu#37967
# ("Update score multipliers based on user feedback", the 2026 rebalance).
# The lazer standardised total is multiplied by the product of the active
# mods' ScoreMultiplier. Rate mods (DT/HT) are truly rate-dependent; these
# are the STANDARD-rate values (DT 1.5x -> 1.23, HT 0.75x -> 0.55). Any mod
# not listed (NF/SD/PF/AT/cosmetic) is 1.0.  Keyed by the osu! mod bit.
_MOD_SCORE_MULT = {
    1 << 1:  0.80,    # EZ  Easy         (was 0.5)
    1 << 3:  1.04,    # HD  Hidden       (was 1.06)
    1 << 4:  1.09,    # HR  Hard Rock    (was 1.06)
    1 << 6:  1.23,    # DT  Double Time  (was 1.1, standard 1.5x)
    1 << 8:  0.55,    # HT  Half Time    (was 0.3, standard 0.75x)
    1 << 9:  1.23,    # NC  Nightcore == DT
    1 << 10: 1.20,    # FL  Flashlight   (was 1.12)
    1 << 12: 0.95,    # SO  Spun Out     (was 0.9)
}


def mods_score_multiplier(mods: int) -> float:
    """Product of the active mods' ScoreV3 multipliers (ppy/osu#37967)."""
    mods = int(mods or 0)
    if mods & (1 << 9):        # NC stored as DT|NC — count the speed mult once
        mods &= ~(1 << 6)
    m = 1.0
    for bit, mult in _MOD_SCORE_MULT.items():
        if mods & bit:
            m *= mult
    return m


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
    # running accuracy RATIO after this judgment. Stable engine: the
    # standard 300/100/50 object formula; lazer engine: ScoreProcessor's
    # tick/tail-inclusive accuracy (slider parts between popups fold into
    # the NEXT popup's value; the LAST event carries the reconciled final).
    acc_after: float = 1.0
    # Map-time (ms) at which this judgment's SCORE is EARNED, for the running
    # score CURVE. Differs from time_ms (the popup time) only for a STABLE
    # slider — its aggregate popup shows at the slider END, but the combo/score
    # is earned at the HEAD. None = same as time_ms. The panel HUD uses time_ms
    # (unchanged); the versus score timeline uses this so a leading slider does
    # not read 0 until it ends. lazer already judges sliders at the head.
    score_time_ms: float | None = None


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
                         f"{engine.split()[0]} spinner model "
                         "(reconcile keeps totals exact)")
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
    # spinner diagnostics: the judged requirement and the achieved amount,
    # in the ENGINE's own unit (stable: half-spins scored vs required;
    # lazer: full spins achieved vs SpinsRequired)
    spin_req: int = 0
    spin_got: float = 0.0


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
                self._evaluate_spinner(s)
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

    # ---- phase 3: spinners (stable + lazer ports) --------------------------------------

    def _clock_rate(self) -> float:
        """Effective clock rate of the play: lazer custom speed_change
        (meta.rate_override) wins, else the legacy bitmask (DT/NC 1.5,
        HT 0.75), else 1.0. Rate-RAMP (WU/WD) replays fall back to this
        base rate for spinner physics — noted approximation."""
        m = self.meta
        if m is None:
            return 1.0
        override = getattr(m, "rate_override", None)
        if override:
            return float(override)
        mods = int(getattr(m, "mods", 0) or 0)
        if mods & ((1 << 6) | (1 << 9)):        # DT / NC
            return 1.5
        if mods & (1 << 8):                     # HT
            return 0.75
        return 1.0

    def _evaluate_spinner(self, s: _ObjSim) -> None:
        """Judge one spinner with the engine matching the replay — the
        stable/lazer requirement formulas AND tier ladders differ (module
        docstring has both models; the shared old model under-credited every
        spin ~2× and popped bogus 50s — the Mayume bug)."""
        if self.lazer:
            requirement, got, tier = self._spin_lazer(s)
        else:
            requirement, got, tier = self._spin_stable(s)
        s.spin_req = requirement
        s.spin_got = got
        s.final = tier
        s.head_judged = True
        s.head_hit = s.final is not JudgmentKind.MISS
        s.hit_time = s.end if s.head_hit else None
        s.delta = 0.0 if s.head_hit else None

    def _spin_stable(self, s: _ObjSim) -> tuple[int, float, JudgmentKind]:
        """osu!stable spinner scoring — the model every stable replay was
        actually judged under (constants and structure cross-checked against
        danser-go's stable port, which is verified against leaderboard
        replays at scale; danser-go app/rulesets/osu/spinner.go
        processStable/UpdatePostFor):

          * REQUIREMENT = int(duration_s × SpinnerRatio) in HALF-spins,
            SpinnerRatio = DifficultyRate(od, 3, 5, 7.5) (difficulty.py) —
            i.e. 1.5/2.5/3.75 full spins per second, NOT 3/5/7.5.
          * PHYSICS per replay frame strictly inside (start, end): the
            cursor angle around the centre sets a THEORETICAL velocity
            (angleDiff/16.67 ms at stable frame pacing; angleDiff/realDt
            when the frame cadence runs slow); the ACTUAL velocity chases
            it under an acceleration budget (0.00008 + max(0, (5000 −
            duration)/1000/2000) rad/ms² — short spinners spin up faster)
            and a hard ±0.05 rad/ms cap (477 rpm). No key held → the angle
            input is zeroed, so the velocity DECAYS but residual rotation
            still credits (stable behaviour). Zero cursor movement decays
            the theoretical velocity /3 then to 0. SpunOut/Autopilot pin
            the velocity at 0.03 rad/ms. Under rate mods the physics run
            in REAL time (danser GetModifiedTime): the acceleration budget
            and the slow-cadence velocity divide by the clock rate while
            rotation credits over MAP-time dt — this is why DT stable
            replays legitimately bank ~rate× the raw cursor angle (a real
            HD,DT ESSE CARA! replay in the test set NEEDS this to reach
            its recorded 300s).
          * SCORING: rotationCountF accumulates |velocity·dt|/π (HALF-spin
            units); scoringRotationCount ticks once per completed half-spin.
          * TIERS at the spinner end — modern scoring (the cuttingedge/
            20190510.2 change, keyed on .osr game_version): req == 0 → 300,
            scoring ≥ req+1 → 300, ≥ req−1 → 100, ≥ req//4 → 50, else miss
            (yes: a req-1 spinner can never miss, a req≤3 spinner never
            drops below 50 — stable's real post-2019 leniency). Pre-2019
            replays: ≥ req+2 → 300, ≥ req+1 → 100, ≥ req → 50, else miss.
        """
        cx, cy = SPINNER_CENTRE
        speed = self._clock_rate()
        dur = s.end - s.start
        requirement = int(dur / 1000.0 * self.diff.spinner_ratio)
        max_accel = 0.00008 + max(0.0, (5000.0 - dur) / 1000.0 / 2000.0)
        mods = int(getattr(self.meta, "mods", 0) or 0)
        auto_spin = bool(mods & ((1 << 12) | (1 << 13)))    # SO / Autopilot
        relax = bool(mods & (1 << 7))
        vel = theo = 0.0
        last_angle = 0.0
        seen_angle = False
        zero_count = 0
        frame_var = SPINNER_FRAME_TIME
        rot_f = 0.0                 # accumulated half-spins (float)
        scoring = 0
        last_count = 0
        prev_t: float | None = None
        for f in self.frames:
            t = f.time_ms
            if not (s.start < t < s.end):
                prev_t = t          # frame pacing spans the whole replay
                continue
            dt = (t - prev_t) if prev_t is not None else SPINNER_FRAME_TIME
            prev_t = t
            # actual velocity chases the previous frame's theoretical one
            accel = max_accel * dt / speed
            if auto_spin:
                vel = SPINNER_AUTO_VEL
            elif theo > vel:
                a = accel / 4.0 if (vel < 0.0 and relax) else accel
                vel += min(theo - vel, a)
            else:
                a = accel / 4.0 if (vel > 0.0 and relax) else accel
                vel -= min(vel - theo, a)
            vel = max(-SPINNER_VEL_CAP, min(vel, SPINNER_VEL_CAP))
            # new theoretical velocity from this frame's cursor angle
            ang = math.atan2(f.y - cy, f.x - cx)
            if not seen_angle:
                last_angle = ang    # first frame contributes no delta
                seen_angle = True
            d = ang - last_angle
            if d < -math.pi:
                d += 2.0 * math.pi
            elif d > math.pi:
                d -= 2.0 * math.pi
            decay = 0.999 ** dt
            frame_var = decay * frame_var + (1.0 - decay) * dt
            if d == 0.0:
                zero_count += 1
                theo = theo / 3.0 if zero_count < 2 else 0.0
            else:
                zero_count = 0
                if not (f.keys & (_CH1 | _CH2)) and not relax:
                    d = 0.0
                if abs(d) < math.pi:
                    if frame_var / speed > SPINNER_FRAME_TIME * 1.04:
                        theo = (d / (dt / speed)) if dt > 0 else 0.0
                    else:
                        theo = d / SPINNER_FRAME_TIME
                else:
                    theo = 0.0
            last_angle = ang
            rot_f += abs(vel * dt) / math.pi
            count = int(rot_f)
            if count != last_count:
                scoring += 1
                last_count = count
        gv = int(getattr(self.meta, "game_version", 0) or 0)
        if 0 < gv < NEW_SPINNER_SCORING_VERSION:    # pre-2019 stable tiers
            if scoring >= requirement + 2:
                tier = JudgmentKind.HIT300
            elif scoring >= requirement + 1:
                tier = JudgmentKind.HIT100
            elif scoring >= requirement:
                tier = JudgmentKind.HIT50
            else:
                tier = JudgmentKind.MISS
        elif requirement == 0 or scoring >= requirement + 1:
            tier = JudgmentKind.HIT300
        elif scoring >= requirement - 1:
            tier = JudgmentKind.HIT100
        elif scoring >= requirement // 4:
            tier = JudgmentKind.HIT50
        else:
            tier = JudgmentKind.MISS
        return requirement, float(scoring), tier

    def _spin_lazer(self, s: _ObjSim) -> tuple[int, float, JudgmentKind]:
        """lazer spinner scoring (ppy/osu master — Objects/Spinner.cs
        ApplyDefaultsToSelf + Drawables/DrawableSpinner.cs Progress/
        CheckForResult + Skinning/SpinnerRotationTracker semantics, cross-
        checked against danser-go processLazer):

          * SpinsRequired = int(minRps × duration_s + duration_error),
            minRps = DifficultyRange(od, CLEAR_RPM_RANGE 90/150/225)/60
            (difficulty.py's lz_spinner_min_rps) — FULL spins.
          * rotation: per-frame angle delta in degrees (±180-wrapped),
            counted while a button is held (or Relax), ×clock-rate (the
            tracker rate-compensates so DT/HT spins score the same); a
            spin completes when the MAX |rotation| within the current
            spin reaches 360° — direction reversals do not stack.
          * Progress = TotalRotation/360/SpinsRequired (1 when none
            required); ≥1 → 300, >0.9 → 100, >0.75 → 50, else miss.
        """
        cx, cy = SPINNER_CENTRE
        speed = self._clock_rate()
        dur = s.end - s.start
        requirement = int(self.diff.lz_spinner_min_rps * (dur / 1000.0)
                          + LAZER_SPIN_DURATION_ERROR)
        mods = int(getattr(self.meta, "mods", 0) or 0)
        relax = bool(mods & (1 << 7))
        total_acc = 0.0             # signed accumulated degrees
        acc_at_completion = 0.0     # accumulated value when the last spin closed
        cur_max = 0.0               # max |rotation| within the current spin
        rotation_count = 0
        last_angle = 0.0
        seen_angle = False
        for f in self.frames:
            if f.time_ms < s.start or f.time_ms > s.end:
                continue
            ang = math.degrees(math.atan2(f.y - cy, f.x - cx))
            d = (ang - last_angle) if seen_angle else 0.0
            seen_angle = True
            last_angle = ang
            if d > 180.0:
                d -= 360.0
            elif d < -180.0:
                d += 360.0
            if not (f.keys & (_CH1 | _CH2)) and not relax:
                continue
            d *= speed
            if d == 0.0:
                continue
            total_acc += d
            cur_max = max(cur_max, abs(total_acc - acc_at_completion))
            while cur_max >= 360.0:
                direction = 1.0 if (total_acc - acc_at_completion) >= 0.0 else -1.0
                rotation_count += 1
                acc_at_completion += direction * 360.0
                cur_max = abs(total_acc - acc_at_completion)
        total_rotation = 360.0 * rotation_count + cur_max
        spins = total_rotation / 360.0
        progress = 1.0 if requirement <= 0 else spins / requirement
        if progress >= 1.0:
            tier = JudgmentKind.HIT300
        elif progress > 0.9:
            tier = JudgmentKind.HIT100
        elif progress > 0.75:
            tier = JudgmentKind.HIT50
        else:
            tier = JudgmentKind.MISS
        return requirement, spins, tier

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
        """(time, kind, is_object, sim, base, base_max, combo_mode, hit,
        part_kind) events. combo_mode: 'inc' = +1 on hit / RESET on miss
        (circles, spinners, slider heads, ticks and repeats —
        head/tick/repeat misses are all sliderbreaks; the head's reset
        lands at its miss moment, the window close); 'inc_noreset' = +1 on
        hit, no reset on miss (the slider TAIL — stable's only lenient
        part); 'none' = no combo contribution (the slider's aggregate
        judgment — stable combo comes from the parts). part_kind is the
        slider PartOutcome kind ("head"|"tick"|"repeat"|"tail") for part
        events, None for object judgments — the lazer accuracy
        accumulation weighs ticks/tails by it."""
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
                               BASE_LARGE_TICK, mode, p.hit, p.kind))
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
                           s.final is not JudgmentKind.MISS, None))
            else:
                t = s.hit_time if s.hit_time is not None else \
                    (s.deadline if s.kind == "circle" else s.end)
                ev.append((t, s.final, True, s,
                           BASE_SCORE[s.final.value], 300, "inc",
                           s.final is not JudgmentKind.MISS, None))
        ev.sort(key=lambda e: e[0])
        return ev

    def _build_events(
            self, sims: list[_ObjSim],
    ) -> tuple[list[JudgmentEvent], int, list[tuple[float, int]]]:
        """Combo (stable semantics), accuracy and lazer-standardised score
        over the full event lattice; emits one JudgmentEvent per OBJECT
        judgment (the popups). ACCURACY is engine-split: the stable path
        keeps the standard std formula over object judgments only; the
        LAZER path is lazer's ScoreProcessor.Accuracy — slider ticks and
        repeats (LargeTickHit, 30) and tails (SliderTailHit, 150) fold
        into the running numerator/denominator alongside the 300-base
        object judgments (heads excluded: their accuracy IS the object
        judgment; spinner bonus never affects accuracy). That is why a
        lazer render's accuracy reads higher than the header-count formula
        on slider maps (the forum bug: 98.88% shown vs the 98.91% the
        player saw — the header formula on a LAZER play). Returns (events,
        max_combo, combo_timeline) — max combo is tracked on the lattice
        so peaks inside a broken slider still count, and the timeline
        records every part-level combo CHANGE (the HUD's counter)."""
        lattice = self._lattice(sims)

        # perfect-run combo portion (denominator of the combo term)
        max_combo_portion = 0.0
        max_base_total = 0.0
        c = 0
        for _t, _k, _is_obj, _s, _b, bmax, mode, _hit, _pk in lattice:
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
        # lazer ScoreProcessor bonus portion: SmallBonus/LargeBonus spins add
        # a flat term ON TOP of the 500k combo + 500k accuracy split. The sim
        # does not model individual bonus spins, so fold the .osr's achieved
        # bonus counts in as a constant. For a near-empty play whose combo AND
        # accuracy portions both round to 0, this bonus is the ENTIRE score —
        # without it the honesty gate sees sim 0 vs a nonzero replay total.
        _st = self.lazer_stats
        _bonus_portion = (SMALL_BONUS_SCORE * getattr(_st, "small_bonus", 0)
                          + LARGE_BONUS_SCORE * getattr(_st, "large_bonus", 0)
                          ) if _st is not None else 0.0
        # ScoreV3 total is scaled by the play's mod multiplier (ppy/osu#37967)
        # — EZ 0.8x, HR 1.09x, DT 1.23x, HT 0.55x, HD 1.04x, FL 1.2x, SO 0.95x.
        # This was previously NOT applied, so EZ scores weren't reduced and
        # HR/DT weren't boosted — the versus-board discrepancy Red flagged.
        _mod_mult = mods_score_multiplier(getattr(self.meta, "mods", 0) or 0)
        # lazer running accuracy state (ScoreProcessor.Accuracy — see the
        # docstring). Stays untouched on the stable path so a stable
        # replay's numbers are float-identical to the pre-split code.
        acc_num = 0.0
        acc_den = 0.0
        for t, kind, is_obj, s, b, bmax, mode, hit, part_kind in lattice:
            cur_base += b
            cur_max_base += bmax
            if (self.lazer and not is_obj
                    and part_kind in ("tick", "repeat", "tail")):
                w = LAZER_TAIL_ACC if part_kind == "tail" \
                    else BASE_LARGE_TICK
                if hit:
                    acc_num += w
                acc_den += w
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
            if self.lazer:
                # lazer: the object judgment joins the tick/tail terms
                # (ScoreProcessor uses THIS accuracy in the score formula
                # too, so the standardised-score trajectory follows it)
                acc_num += BASE_SCORE[kind.value]
                acc_den += 300.0
                acc = acc_num / acc_den if acc_den > 0 else 1.0
            else:
                acc = obj_base / (300.0 * obj_n)
            if max_combo_portion > 0 and max_base_total > 0:
                score = (500_000.0 * acc * (combo_portion / max_combo_portion)
                         + 500_000.0 * (acc ** 5) * (cur_max_base / max_base_total)
                         + _bonus_portion) * _mod_mult
            else:
                score = 0.0
            px, py = self._popup_pos(s)
            # STABLE slider: score is earned at the head (s.hit_time) though the
            # aggregate popup lands at s.end (== t here). Tag score_time_ms so
            # the versus score curve credits the head; the popup keeps t.
            _score_t = (float(s.hit_time) if (s.kind == "slider" and not self.lazer
                        and s.hit_time is not None and s.hit_time < t) else None)
            events.append(JudgmentEvent(
                time_ms=t, kind=kind, object_id=s.idx, x=px, y=py,
                combo_after=combo, score_after=int(round(score)),
                acc_after=acc, score_time_ms=_score_t))
        if self.lazer and events:
            # FINAL-ACC PIN (the honesty reconcile's display rule): parts
            # can land AFTER the last object judgment (a map ending on a
            # slider judges its tail past the head-time popup), so the last
            # event's running value may miss them — close the ratio over
            # the WHOLE lattice, and prefer the .osr ScoreInfo's canonical
            # accuracy when the replay carries one (post-reconcile the two
            # agree exactly; the canonical value also covers the CL-adjacent
            # cases the sim approximates). The HUD's final frame, the
            # results screen and the site card then all read the same
            # number the player saw in lazer.
            final = acc_num / acc_den if acc_den > 0 else None
            canonical = (getattr(self.meta, "lazer_accuracy", None)
                         if self.meta is not None else None)
            if self.do_reconcile and canonical is not None:
                final = canonical
            if final is not None:
                events[-1] = dataclasses.replace(events[-1],
                                                 acc_after=final)
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
