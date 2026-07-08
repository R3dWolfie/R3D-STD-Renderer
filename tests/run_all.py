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
    "tests.test_slider_body",
    "tests.test_playfield_pipeline",
    "tests.test_scene",
    "tests.test_hud",
    "tests.test_background",
    "tests.test_skin_elements",
    "tests.test_markers",
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
