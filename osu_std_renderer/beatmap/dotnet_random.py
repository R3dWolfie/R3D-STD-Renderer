"""Port of .NET's seeded ``System.Random`` (Knuth's subtractive PRNG).

osu!lazer's ``OsuModRandom`` positions objects with ``new Random((int)Seed)``
(``osu.Game.Rulesets.Osu/Mods/OsuModRandom.cs`` — ``random = new Random((int)
Seed.Value)``), i.e. the framework System.Random, NOT osu's xorshift
``LegacyRandom`` (that one is ``curves/legacy_random.py``, used only by the
catch banana/droplet offsets). So to reproduce a RANDOM-mod replay's exact
object positions we must reproduce .NET's number stream bit-for-bit.

.NET's seeded ``Random`` uses the SAME algorithm on .NET Framework and
.NET Core/5+ (the parameterless ctor switched to xoshiro in .NET 6, but a
SEEDED ctor keeps the legacy "compat" implementation for determinism — lazer
always seeds it). Ported from the reference source:

  * dotnet/runtime  src/libraries/System.Private.CoreLib/src/System/Random.Net5CompatImpl.cs
    (``CompatPrng`` — EnsureInitialized / InternalSample / Sample / GetSampleForLargeRange)
  * historically identical to .NET Framework ``System.Random`` (Reference Source):
    MSEED 161803398, MBIG int.MaxValue, the 55-entry Knuth SeedArray with the
    ``(21*i)%55`` fill and the 4-pass warm-up, inext=0 / inextp=21.

Verified bit-for-bit against dotnet/runtime's own test vectors
(``System.Runtime.Extensions.Tests`` — ``Random.ExpectedValues``, the hardcoded
``new Random(seed).Next()`` sequences for seeds 0..19); see
``tests/test_pos_mods.py::test_dotnet_random_matches_dotnet_runtime_vectors``.

All arithmetic below is plain Python int/float; the generator's state is pure
32-bit signed integer subtraction (values in ``[0, int.MaxValue)``), so no
masking is needed — Python ints reproduce the C# ``int`` math exactly.
"""
from __future__ import annotations

_MBIG = 2147483647          # int.MaxValue (Knuth's MBIG)
_MSEED = 161803398          # Random's magic seed (≈ Phi-derived)
_INV_MBIG = 1.0 / _MBIG     # Sample()'s (1.0 / int.MaxValue) scaling


class DotNetRandom:
    """.NET ``System.Random`` seeded with a signed 32-bit ``seed``.

    Only the members ``OsuModRandom`` consumes are exposed: ``next()`` (==
    ``Random.Next()`` == ``InternalSample()``) and ``next_double()`` (==
    ``Random.NextDouble()`` == ``Sample()``). Both advance the identical state
    the game does, in the identical order.
    """

    def __init__(self, seed: int):
        # C# casts the seed to int; lazer passes ``(int)Seed.Value``. Normalise
        # any Python int into the signed 32-bit range the ctor sees.
        seed = ((int(seed) + 0x80000000) & 0xFFFFFFFF) - 0x80000000

        seed_array = [0] * 56
        # subtraction = (seed == int.MinValue) ? int.MaxValue : Math.Abs(seed)
        subtraction = _MBIG if seed == -2147483648 else abs(seed)
        mj = _MSEED - subtraction
        seed_array[55] = mj
        mk = 1
        ii = 0
        for i in range(1, 55):
            # dotnet: if ((ii += 21) >= 55) ii -= 55;  (== (21*i) % 55)
            ii += 21
            if ii >= 55:
                ii -= 55
            seed_array[ii] = mk
            mk = mj - mk
            if mk < 0:
                mk += _MBIG
            mj = seed_array[ii]
        for _k in range(1, 5):
            for i in range(1, 56):
                n = i + 30
                if n >= 55:
                    n -= 55
                seed_array[i] -= seed_array[1 + n]
                if seed_array[i] < 0:
                    seed_array[i] += _MBIG
        self._seed_array = seed_array
        self._inext = 0
        self._inextp = 21

    def _internal_sample(self) -> int:
        loc_inext = self._inext + 1
        if loc_inext >= 56:
            loc_inext = 1
        loc_inextp = self._inextp + 1
        if loc_inextp >= 56:
            loc_inextp = 1
        sa = self._seed_array
        ret = sa[loc_inext] - sa[loc_inextp]
        if ret == _MBIG:
            ret -= 1
        if ret < 0:
            ret += _MBIG
        sa[loc_inext] = ret
        self._inext = loc_inext
        self._inextp = loc_inextp
        return ret

    def next(self) -> int:
        """``Random.Next()`` — a non-negative int in ``[0, int.MaxValue)``."""
        return self._internal_sample()

    def sample(self) -> float:
        """``Random.Sample()`` — ``InternalSample() * (1.0 / int.MaxValue)``."""
        return self._internal_sample() * _INV_MBIG

    def next_double(self) -> float:
        """``Random.NextDouble()`` — a double in ``[0, 1)`` (== ``Sample()``)."""
        return self.sample()
