"""GPU draw path — headless EGL. GL modules import moderngl at import time,
so they are exported lazily; playfield math is dependency-free."""
from .playfield import (OSU_HEIGHT, OSU_WIDTH, PLAYFIELD_INSET,  # noqa: F401
                        PlayfieldCamera)

__all__ = ["OSU_HEIGHT", "OSU_WIDTH", "PLAYFIELD_INSET", "PlayfieldCamera",
           "Sprite", "SpriteRenderer", "create_context"]


def __getattr__(name):  # lazy: keep parse-only usage moderngl-free
    if name in ("Sprite", "SpriteRenderer"):
        from .gl import Sprite, SpriteRenderer
        return {"Sprite": Sprite, "SpriteRenderer": SpriteRenderer}[name]
    if name == "create_context":
        from .context import create_context
        return create_context
    raise AttributeError(name)
