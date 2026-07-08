"""Difficulty math — exact port of RENDER_PLAN.md §2.4
(app/beatmap/difficulty/difficulty.go).

Every formula here is the reference renderer's, including the float32-cast
quirk in DifficultyRate and the *1.00041 "weird allowance" on CircleRadius.
"""
from __future__ import annotations

import math
import struct

# §2.4 constants (difficulty.go:14)
HIT_FADE_IN = 400.0
HIT_FADE_OUT = 240.0
HITTABLE_RANGE = 400.0
RESULT_FADE_IN = 120.0
RESULT_FADE_OUT = 600.0
POST_EMPT = 500.0


def _f32(x: float) -> float:
    """Round-trip through IEEE-754 binary32, exactly like Go's float32(x)."""
    return struct.unpack("f", struct.pack("f", x))[0]


def difficulty_rate(diff: float, min_v: float, mid_v: float, max_v: float) -> float:
    """§2.4 'the core interpolator' (difficulty.go:643).

    diff is cast through float32 FIRST — the reference calls this the
    "stable quirk"; dropping it shifts AR/OD-derived values on maps with
    non-representable decimal stats (e.g. AR 9.3).
    """
    diff = _f32(diff)
    if diff > 5:
        return mid_v + (max_v - mid_v) * (diff - 5) / 5
    if diff < 5:
        return mid_v - (mid_v - min_v) * (5 - diff) / 5
    return mid_v


def diff_from_rate(rate: float, min_v: float, mid_v: float, max_v: float) -> float:
    """§2.4 DiffFromRate — exact inverse of difficulty_rate (same f32 quirk)."""
    rate = _f32(rate)
    min_step = (min_v - mid_v) / 5
    max_step = (mid_v - max_v) / 5
    if rate > mid_v:
        return -(rate - min_v) / min_step
    return 5.0 + (mid_v - rate) / max_step


class Mods:
    """§2.4 mod bitmask (mods.go, 1<<(iota-1))."""
    NOMOD = 0
    NOFAIL = 1 << 0
    EASY = 1 << 1
    TOUCH_DEVICE = 1 << 2
    HIDDEN = 1 << 3
    HARD_ROCK = 1 << 4
    SUDDEN_DEATH = 1 << 5
    DOUBLE_TIME = 1 << 6
    RELAX = 1 << 7
    HALF_TIME = 1 << 8
    NIGHTCORE = 1 << 9          # implies DOUBLE_TIME
    FLASHLIGHT = 1 << 10
    AUTOPLAY = 1 << 11
    SPUN_OUT = 1 << 12
    RELAX2 = 1 << 13            # autopilot
    PERFECT = 1 << 14           # implies SUDDEN_DEATH
    KEY4 = 1 << 15
    KEY5 = 1 << 16
    KEY6 = 1 << 17
    KEY7 = 1 << 18
    KEY8 = 1 << 19
    FADE_IN = 1 << 20
    RANDOM = 1 << 21
    CINEMA = 1 << 22
    TARGET = 1 << 23
    KEY9 = 1 << 24
    KEY_COOP = 1 << 25
    KEY1 = 1 << 26
    KEY3 = 1 << 27
    KEY2 = 1 << 28
    SCORE_V2 = 1 << 29
    MIRROR = 1 << 30


class Difficulty:
    """§2.4 Difficulty struct: base stats, mod-adjusted stats, derived values.

    calculate() implements the exact block quoted in the plan:

        HardRock: ar=min(ar*1.4,10) cs=min(cs*1.3,10) od=min(od*1.4,10) hp=min(hp*1.4,10)
        Easy:     ar/=2 cs/=2 od/=2 hp/=2
        CircleRadiusU = DifficultyRate(cs, 54.4, 32, 9.6)
        CircleRadius  = CircleRadiusU * 1.00041
        CircleScaleL  = (1.0 - 0.7*(cs-5)/5)/2 * 1.00041   (float32)
        CircleRadiusL = CircleScaleL * 64
        PreemptU   = DifficultyRate(ar, 1800, 1200, 450)
        Preempt    = floor(PreemptU)
        TimeFadeIn = 400 * min(1, PreemptU/450)
        Hit50U/100U/300U = DifficultyRate(od, 200/140/80, 150/100/50, 100/60/20)
        DT → BaseModSpeed 1.5 ; HT → 0.75 ; else 1
        ARReal = DiffFromRate(PreemptU/Speed, 1800, 1200, 450)
        ODReal = (80 - Hit300U/Speed) / 6
    """

    def __init__(self, ar: float = 5, od: float = 5, cs: float = 5, hp: float = 5):
        self.base_ar = ar
        self.base_od = od
        self.base_cs = cs
        self.base_hp = hp
        self.mods = Mods.NOMOD
        self.custom_speed: float | None = None  # SpeedSettings custom rate
        self.calculate()

    # --- §2.4 calculate() (difficulty.go:96) --------------------------------
    def calculate(self) -> None:
        ar, od, cs, hp = self.base_ar, self.base_od, self.base_cs, self.base_hp
        if self.mods & Mods.HARD_ROCK:
            ar = min(ar * 1.4, 10.0)
            cs = min(cs * 1.3, 10.0)
            od = min(od * 1.4, 10.0)
            hp = min(hp * 1.4, 10.0)
        if self.mods & Mods.EASY:
            ar /= 2
            cs /= 2
            od /= 2
            hp /= 2
        self.ar, self.od, self.cs, self.hp = ar, od, cs, hp

        # circle size → radius (osu!px @ CS 0/5/10 = 54.4/32/9.6)
        self.circle_radius_u = difficulty_rate(cs, 54.4, 32, 9.6)
        self.circle_radius = self.circle_radius_u * 1.00041  # "weird allowance"
        self.circle_scale_l = _f32((1.0 - 0.7 * (cs - 5) / 5) / 2 * 1.00041)  # lazer
        self.circle_radius_l = self.circle_scale_l * 64

        # approach rate → preempt/fade-in (ms @ AR 0/5/10 = 1800/1200/450)
        self.preempt_u = difficulty_rate(ar, 1800, 1200, 450)
        self.preempt = math.floor(self.preempt_u)
        self.time_fade_in = 400 * min(1.0, self.preempt_u / 450)

        # overall difficulty → half-window ms
        self.hit50_u = difficulty_rate(od, 200, 150, 100)
        self.hit100_u = difficulty_rate(od, 140, 100, 60)
        self.hit300_u = difficulty_rate(od, 80, 50, 20)
        self.hit50 = int(self.hit50_u)    # Go int64() truncation
        self.hit100 = int(self.hit100_u)
        self.hit300 = int(self.hit300_u)

        # spinners
        self.spinner_ratio = difficulty_rate(od, 3, 5, 7.5)
        self.lz_spinner_min_rps = difficulty_rate(od, 90, 150, 225) / 60
        self.lz_spinner_max_rps = difficulty_rate(od, 250, 380, 430) / 60

        # rate mods
        if self.mods & (Mods.DOUBLE_TIME | Mods.NIGHTCORE):
            self.base_mod_speed = 1.5
        elif self.mods & Mods.HALF_TIME:
            self.base_mod_speed = 0.75
        else:
            self.base_mod_speed = 1.0
        self.speed = self.custom_speed if self.custom_speed else self.base_mod_speed

        self.ar_real = diff_from_rate(self.preempt_u / self.speed, 1800, 1200, 450)
        self.od_real = (80 - self.hit300_u / self.speed) / 6

    # --- setters (re-derive like danser's SetAR/SetCS/…) ---------------------
    def set_ar(self, ar: float) -> None:
        self.base_ar = _clamp01_10(ar)
        self.calculate()

    def set_od(self, od: float) -> None:
        self.base_od = _clamp01_10(od)
        self.calculate()

    def set_cs(self, cs: float) -> None:
        self.base_cs = _clamp01_10(cs)
        self.calculate()

    def set_hp(self, hp: float) -> None:
        self.base_hp = _clamp01_10(hp)
        self.calculate()

    def set_mods(self, mods: int) -> None:
        if mods & Mods.NIGHTCORE:
            mods |= Mods.DOUBLE_TIME
        if mods & Mods.PERFECT:
            mods |= Mods.SUDDEN_DEATH
        self.mods = mods
        self.calculate()

    def add_mod(self, mod: int) -> None:
        self.set_mods(self.mods | mod)

    def check_mod(self, mod: int) -> bool:
        return bool(self.mods & mod)

    def get_modified_time(self, t: float) -> float:
        """§2.4 GetModifiedTime(t) = t / Speed."""
        return t / self.speed

    def get_radius(self) -> float:
        """§2.4 GetRadius(): Lazer→CircleRadiusL; Autopilot→100; else CircleRadius.

        We render stable-style, so the stable radius is the default; the lazer
        branch is kept for future lazer-fidelity work.
        """
        if self.mods & Mods.RELAX2:
            return 100.0
        return self.circle_radius


def _clamp01_10(v: float) -> float:
    """§2.2 parseDifficulty: AR/CS/OD/HP clamped to [0,10]."""
    return max(0.0, min(10.0, v))
