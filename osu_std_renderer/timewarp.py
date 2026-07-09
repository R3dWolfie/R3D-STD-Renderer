"""Wall<->gameplay time transform for the WU/WD rate ramp (ModTimeRamp).

osu!(lazer) Wind Up / Wind Down (osu.Game/Rulesets/Mods/ModTimeRamp.cs,
ModWindUp.cs, ModWindDown.cs) continuously RAMP the clock rate over the map,
unlike DT/HT's constant ``SpeedChange``. The instantaneous rate is a LINEAR
interpolation of ``InitialRate`` -> ``FinalRate`` in GAMEPLAY (map) time:

    ApplyToBeatmap:
        beginRampTime = firstObject.StartTime
        finalRateTime = firstObject.StartTime
                        + FINAL_RATE_PROGRESS * (lastObjectEnd - firstObject.StartTime)
        # FINAL_RATE_PROGRESS = 0.75  -> the ramp completes at 75% of the map,
        #   then the rate is PINNED at FinalRate for the last quarter.
    ApplyToRate(time):
        amount = (time - beginRampTime) / Math.Max(1, finalRateTime - beginRampTime)
        rate   = InitialRate + (FinalRate - InitialRate) * clamp(amount, 0, 1)
        # lazer additionally does Math.Round(rate, 2); we keep the UN-rounded
        #   linear rate so the closed-form transform below is EXACTLY the
        #   integral of the rate the clock discretizes (the 0.01 quantization
        #   is an imperceptible, non-accumulating <0.5% deviation — see report).

``time`` is the gameplay CLOCK's current time, which itself advances at the
ramped rate, so the wall<->map relation obeys the ODE  dg/dw = rate(g).  With a
rate that is LINEAR in map time this integrates to a CLOSED FORM that is
piecewise: linear outside the ramp window (constant rate) and LOGARITHMIC /
EXPONENTIAL inside it (a linear-in-g rate makes g an exponential function of
wall time w, NOT quadratic — quadratic would require the rate to be linear in
WALL time; lazer anchors it to the gameplay clock, so exponential is the
faithful transform). This module owns that transform so the record clock,
audio warp and every wall<->map conversion in the renderer share ONE math.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# osu.Game/Rulesets/Mods/ModTimeRamp.cs: FINAL_RATE_PROGRESS
FINAL_RATE_PROGRESS = 0.75


@dataclass
class TimeWarp:
    """Bidirectional map<->wall time transform for a linear rate ramp.

    ``initial`` / ``final`` are the EFFECTIVE absolute clock rates at the ramp
    endpoints (WU: 1.0 -> 1.5; WD: 1.0 -> 0.75; each already multiplied by any
    base speed — 1.0 for a pure WU/WD, since ramps are incompatible with
    DT/HT). ``g0`` is ``beginRampTime`` (first object start, map ms); ``g1`` is
    ``finalRateTime`` (g0 + 0.75*(lastObjectEnd - g0), map ms).

    Wall time is anchored at 0 == map time ``g0`` (``to_wall(g0) == 0``). The
    renderer works with DIFFERENCES of ``to_wall`` (relative to its own
    render-start anchor), so the absolute wall origin is irrelevant.
    """
    initial: float
    final: float
    g0: float
    g1: float
    # derived (post-init)
    _denom: float = field(init=False)
    _gm: float = field(init=False)
    _k: float = field(init=False)
    _wm: float = field(init=False)

    def __post_init__(self) -> None:
        a, b = float(self.initial), float(self.final)
        if a <= 0.0 or b <= 0.0:
            raise ValueError(f"ramp rates must be positive (got {a}, {b})")
        self.initial, self.final = a, b
        # Math.Max(1, finalRateTime - beginRampTime): the ramp spans
        # [g0, g0+denom]; a degenerate map (<2 objects, g1<=g0) collapses to a
        # 1 ms window -> effectively an instant jump to the final rate.
        self._denom = max(1.0, self.g1 - self.g0)
        self._gm = self.g0 + self._denom
        self._k = (b - a) / self._denom            # d(rate)/d(map ms)
        # wall time at the ramp end g_m (relative to wall 0 at g0)
        if abs(self._k) < 1e-15:
            self._wm = self._denom / a
        else:
            self._wm = math.log(b / a) / self._k

    # --- instantaneous rate (drives the record clock) ----------------------
    def rate_at(self, g: float) -> float:
        """The effective clock rate at map time ``g`` — a clamped linear
        interp of initial->final, matching lazer's ApplyToRate (sans the
        Math.Round(.,2) quantization; see the module docstring)."""
        amount = (g - self.g0) / self._denom
        if amount <= 0.0:
            return self.initial
        if amount >= 1.0:
            return self.final
        return self.initial + (self.final - self.initial) * amount

    # --- map -> wall -------------------------------------------------------
    def to_wall(self, g: float) -> float:
        """Wall time of map time ``g`` (relative to wall 0 == g0). Monotonic
        increasing. Closed form: linear before g0 (rate ``initial``),
        logarithmic in [g0, g_m], linear after g_m (rate ``final``)."""
        if g <= self.g0:
            return (g - self.g0) / self.initial
        if g >= self._gm:
            return self._wm + (g - self._gm) / self.final
        if abs(self._k) < 1e-15:
            return (g - self.g0) / self.initial
        # w = (1/k) * ln(rate(g) / a),  rate(g) = a + k*(g-g0)
        return math.log(self.rate_at(g) / self.initial) / self._k

    # --- wall -> map -------------------------------------------------------
    def to_map(self, w: float) -> float:
        """Map time at wall time ``w`` (inverse of :meth:`to_wall`). This is
        the exact solution of dg/dw = rate(g): g grows EXPONENTIALLY with wall
        time inside the ramp window."""
        if w <= 0.0:
            return self.g0 + self.initial * w
        if w >= self._wm:
            return self._gm + self.final * (w - self._wm)
        if abs(self._k) < 1e-15:
            return self.g0 + self.initial * w
        # rate = a * exp(k*w); g = g0 + (rate - a)/k
        rate = self.initial * math.exp(self._k * w)
        return self.g0 + (rate - self.initial) / self._k

    # --- helpers -----------------------------------------------------------
    def map_span(self, g_anchor: float, wall_span: float) -> float:
        """Map-time length of a ``wall_span``-ms real-time window that STARTS
        at map time ``g_anchor``. Exact via the transform; reduces to
        ``wall_span * rate`` in a constant-rate region."""
        return self.to_map(self.to_wall(g_anchor) + wall_span) - g_anchor

    def rate_bands(self, m_lo: float, m_hi: float,
                   quant: float = 0.01) -> list[tuple[float, float]]:
        """Partition map interval [m_lo, m_hi] into segments over which the
        rate is ~constant to within ``quant`` (0.01 == lazer's own
        Math.Round(rate, 2) granularity). Returns [(m0, m1), ...] contiguous
        and covering [m_lo, m_hi]: one flat band before g0, ~|final-initial|/
        quant bands across the ramp, one flat band after g_m. Used to warp the
        music piecewise (each band stretched at its average rate)."""
        pts = {float(m_lo), float(m_hi)}
        if m_lo < self.g0 < m_hi:
            pts.add(self.g0)
        if m_lo < self._gm < m_hi:
            pts.add(self._gm)
        lo = max(m_lo, self.g0)
        hi = min(m_hi, self._gm)
        if hi > lo and abs(self.final - self.initial) > 1e-12:
            n = max(1, int(math.ceil(abs(self.final - self.initial) / quant)))
            for i in range(1, n):
                pts.add(lo + (hi - lo) * i / n)
        xs = sorted(p for p in pts if m_lo <= p <= m_hi)
        return [(xs[i], xs[i + 1]) for i in range(len(xs) - 1)
                if xs[i + 1] > xs[i]]

    def rate_fn(self):
        """The bound rate_at, for ScenePlayer's clock (delta*rate(t))."""
        return self.rate_at


def build_time_warp(initial: float, final: float, first_object_start: float,
                    last_object_end: float,
                    base_speed: float = 1.0) -> TimeWarp:
    """Construct a :class:`TimeWarp` from the ramp endpoints and the beatmap's
    first/last object times, applying lazer's 75% ``finalRateTime`` anchor and
    folding any ``base_speed`` (1.0 for a pure WU/WD) into the rates."""
    g0 = float(first_object_start)
    g1 = g0 + FINAL_RATE_PROGRESS * (float(last_object_end) - g0)
    return TimeWarp(initial=initial * base_speed, final=final * base_speed,
                    g0=g0, g1=g1)
