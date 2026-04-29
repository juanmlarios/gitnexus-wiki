"""Language/framework packs.

Each pack adds discrimination niceties on top of the language-agnostic core.
With no matching pack, the wiki is still rendered and verified — just plainer.
"""

from __future__ import annotations

from pathlib import Path

from .base import Pack
from .python import PythonPack


_REGISTRY: list[type[Pack]] = [PythonPack]


def detect_pack(repo, project_root: Path) -> Pack | None:
    """Pick the first registered pack that matches this repo, or None."""
    for cls in _REGISTRY:
        if cls.matches(project_root):
            return cls()
    return None
