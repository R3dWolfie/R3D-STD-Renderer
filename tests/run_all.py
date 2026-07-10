"""Dependency-free test runner (pytest also works on these files):

    python -m tests.run_all
"""
from __future__ import annotations

import importlib
import sys
import traceback

MODULES = [
    "tests.test_difficulty",
    "tests.test_sliderpath",
    "tests.test_parser_synthetic",
    "tests.test_skin_ini",
    "tests.test_ruleset",
    "tests.test_health",
    "tests.test_slider_body",
    "tests.test_playfield_pipeline",
    "tests.test_scene",
    "tests.test_hud",
    "tests.test_background",
    "tests.test_skin_elements",
    "tests.test_markers",
    "tests.test_spinner",
    "tests.test_hitsounds",
    "tests.test_results",
    "tests.test_lazer_results",
    "tests.test_leaderboard",
    # settings-surface phase (2026-07)
    "tests.test_settings",
    "tests.test_effects",
    "tests.test_nightcore",
    "tests.test_pp",
    "tests.test_video_bg",
    "tests.test_hud_extras",
    # Argon league (skinless) gameplay port (2026-07)
    "tests.test_argon",
    # mod visuals: OsuModHidden fades + OsuModFlashlight overlay (2026-07)
    "tests.test_mods",
    # osu!(lazer) Classic mod (CL) switchboard (2026-07)
    "tests.test_classic_mod",
    # osu!(lazer) Difficulty Adjust mod (DA): AR/CS/OD/HP override + extended
    # limits (2026-07)
    "tests.test_difficulty_adjust",
    # osu!(lazer) custom-rate mods (DT/NC/HT/DC speed_change): effective rate
    # over the bitmask + NC/DC pitch vs DT/HT tempo audio (2026-07)
    "tests.test_rate_mods",
    # Relax auto-tap synthesis (RX) + Autopilot path (2026-07)
    "tests.test_relax",
    # FAIL/DEATH handling: detection + animation transforms + F grade (2026-07)
    "tests.test_fail",
    # osu!(lazer) Wind Up / Wind Down (WU/WD, ModTimeRamp): a clock rate that
    # RAMPS over the map — ramp parse + the wall<->map time warp + audio warp
    # + byte-identical non-ramp path (2026-07)
    "tests.test_wind_mods",
    # position mods: Mirror (MR) + Random (RD), with the .NET Random port
    # verified vs dotnet/runtime vectors (2026-07)
    "tests.test_pos_mods",
    # transform-family "fun" mods: GR/DF/SI/WG/TR — per-object entrance
    # animation (visual only; reconcile/geometry untouched) (2026-07)
    "tests.test_transform_mods",
    # approach/circle-appearance mods: FR (Freeze Frame) / AD (Approach
    # Different) / TC (Traceable) — approach-circle scale/timing + circle-fill
    # gate (visual only; reconcile/geometry untouched) (2026-07)
    "tests.test_appearance_mods",
    # screen / cursor-effect visual mods: BR (Barrel Roll) / BM (Bloom) / SY
    # (Synesthesia) / BL (Blinds) / NS (No Scope) / DP (Depth) / BU (Bubbles) —
    # playfield spin, cursor scale/fade, snap colours, screen blinds, per-object
    # depth + hit-position bubbles (visual only; reconcile/geometry untouched)
    "tests.test_screen_mods",
    # cursor-driven object-movement mods: MG (Magnetised, objects pulled TO the
    # cursor) / RP (Repel, objects pushed AWAY) — a stateful per-frame easeTo
    # (Interpolation.DampContinuously) over the recorded cursor; both hide
    # follow points (visual only; reconcile/geometry untouched)
    "tests.test_repel_magnet",
    # Target Practice (TP): a CONVERSION mod — the map is DISCARDED and rebuilt
    # as seeded "target" hit circles on the beat (OsuModTargetPractice via
    # DotNetRandom). The layout is reproduced bit-for-bit from the .osr seed
    # (object 0 re-derived straight from the C#); non-TP renders untouched.
    "tests.test_target_practice",
]


def main() -> int:
    passed = failed = 0
    for mod_name in MODULES:
        mod = importlib.import_module(mod_name)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"PASS {mod_name}.{name}")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"FAIL {mod_name}.{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
