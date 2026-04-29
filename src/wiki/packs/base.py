"""Pack protocol — implement these methods to add a language pack."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Pack(Protocol):
    language: str

    @classmethod
    def matches(cls, project_root: Path) -> bool:
        ...

    def cluster_extras(self, factpack) -> dict:
        """Return template-extra context for a cluster page.

        Expected keys: language, type_labels (mapping of class name to label
        like 'TypedDict' / 'BaseModel' / 'Protocol'), intro (short
        deterministic-source paragraph or empty string).
        """
        ...
