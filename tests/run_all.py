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
