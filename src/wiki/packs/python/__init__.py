"""Python pack."""

from __future__ import annotations

from pathlib import Path

from .discriminator import label_for_class_line


class PythonPack:
    language = "python"

    @classmethod
    def matches(cls, project_root: Path) -> bool:
        if (project_root / "pyproject.toml").exists():
            return True
        if (project_root / "setup.py").exists() or (project_root / "setup.cfg").exists():
            return True
        if (project_root / "requirements.txt").exists():
            return True
        if any(project_root.glob("src/*/__init__.py")):
            return True
        return False

    def cluster_extras(self, factpack) -> dict:
        type_labels: dict[str, str] = {}
        for c in factpack.classes:
            label = label_for_class_line(c.base_line)
            if label:
                type_labels[c.name] = label
        return {
            "language": "python",
            "type_labels": type_labels,
            "intro": "",
        }
