"""Phase 2b: deterministic Jinja rendering. No LLM."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .factpack import ClusterFactPack


def _template_dirs() -> list[Path]:
    """Search order: user pack overrides (added later) > built-in core templates."""
    here = Path(__file__).resolve().parent.parent  # src/wiki/
    return [here / "templates"]


def render_cluster(pack: ClusterFactPack, *, pack_extras: dict | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader([str(p) for p in _template_dirs()]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    tmpl = env.get_template("cluster.md.j2")
    ctx = asdict(pack)
    if pack_extras:
        ctx["pack"] = pack_extras
    else:
        ctx["pack"] = {"language": None, "type_labels": {}, "intro": ""}
    return tmpl.render(**ctx)
