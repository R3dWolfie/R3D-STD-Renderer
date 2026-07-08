"""Headless GL context — adapted from the production catch renderer's gl.py
(osu-catch/osu_catch_renderer/gl.py). NO GLFW, NO X server: a standalone
moderngl EGL context (the reference's hidden GLFW window, §5.1, is replaced
by this — record mode never shows a window anyway).
"""
from __future__ import annotations

import os

try:
    import moderngl
except Exception as e:  # noqa: BLE001
    raise RuntimeError("moderngl is required for the std renderer") from e


def create_context() -> "moderngl.Context":
    """Standalone EGL context, honoring R3D_EGL_DEVICE_INDEX so renders pin
    to the right GPU (pool isolation: e.g. 1070 = index 1 for Pool B). EGL
    ignores CUDA_VISIBLE_DEVICES, so the device must be selected explicitly.
    """
    dev = os.environ.get("R3D_EGL_DEVICE_INDEX", "").strip()
    if dev.isdigit():
        return moderngl.create_context(standalone=True, backend="egl",
                                       device_index=int(dev))
    return moderngl.create_context(standalone=True, backend="egl")
