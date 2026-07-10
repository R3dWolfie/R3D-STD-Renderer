"""Lightweight phase timing + frame-stream hashing for perf work.

Two independent env switches, both OFF by default (zero output change):

  R3D_TIMING=1     accumulate per-phase wall time + counters, print a
                   breakdown to stderr at process exit.
  R3D_FRAME_MD5=1  hash every raw RGB frame pushed to ffmpeg (blake2b)
                   and print one digest at pipe close — bit-identical
                   output proof across perf changes.

The timers themselves stay armed even when disabled (a perf_counter pair
per coarse phase, ~40 pairs/frame ≈ 40 µs — noise at an 11 ms frame), so
the measured build is the shipped build; only the report is gated.
"""
from __future__ import annotations

import atexit
import os
import sys
import time
from collections import defaultdict

TIMING = bool(os.environ.get("R3D_TIMING"))
FRAME_MD5 = bool(os.environ.get("R3D_FRAME_MD5"))

ACC: dict[str, float] = defaultdict(float)
CNT: dict[str, int] = defaultdict(int)


class T:
    """with T("phase"): ... — accumulate wall time under a label."""
    __slots__ = ("name", "t0")

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        ACC[self.name] += time.perf_counter() - self.t0
        CNT[self.name] += 1
        return False


def count(name: str, n: int = 1) -> None:
    CNT[name] += n


def _report() -> None:
    frames = CNT.get("encode_push", 0) or 1
    lines = ["", f"=== R3D_TIMING (over {frames} pushed frames) ==="]
    for name in sorted(ACC, key=lambda k: -ACC[k]):
        tot = ACC[name]
        lines.append(f"  {name:<24} {tot:8.2f}s total  "
                     f"{tot / frames * 1000.0:8.3f} ms/frame  "
                     f"{CNT[name] / frames:8.1f} calls/frame")
    for name in sorted(CNT):
        if name not in ACC:
            lines.append(f"  {name:<24} {CNT[name] / frames:8.1f} /frame  "
                         f"({CNT[name]} total)")
    print("\n".join(lines), file=sys.stderr, flush=True)


if TIMING:
    atexit.register(_report)
