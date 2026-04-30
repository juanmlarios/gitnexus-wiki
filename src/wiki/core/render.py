"""Phase 2b: deterministic Jinja rendering. No LLM."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .factpack import ClusterFactPack, ProcessesFactPack, RepoFactPack


def _env() -> Environment:
    here = Path(__file__).resolve().parent.parent  # src/wiki/
    return Environment(
        loader=FileSystemLoader([str(here / "templates")]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_cluster(pack: ClusterFactPack, *, pack_extras: dict | None = None) -> str:
    tmpl = _env().get_template("cluster.md.j2")
    ctx = asdict(pack)
    ctx["pack"] = pack_extras or {"language": None, "type_labels": {}, "intro": ""}
    return tmpl.render(**ctx)


def render_readme(pack: RepoFactPack, cluster_pages: list, *, generated_at: str) -> str:
    tmpl = _env().get_template("README.md.j2")
    return tmpl.render(
        repo=pack.repo_name,
        stats=pack.stats,
        top_level_dirs=[asdict(d) for d in pack.top_level_dirs],
        communities=[asdict(c) for c in pack.communities],
        cluster_pages=[asdict(p) for p in cluster_pages],
        process_count=pack.process_count,
        generated_at=generated_at,
    )


def render_architecture(pack: RepoFactPack) -> str:
    tmpl = _env().get_template("architecture.md.j2")
    return tmpl.render(
        repo=pack.repo_name,
        stats=pack.stats,
        top_level_dirs=[asdict(d) for d in pack.top_level_dirs],
        communities=[asdict(c) for c in pack.communities],
        type_surface=[asdict(t) for t in pack.type_surface],
        longest_processes=[asdict(p) for p in pack.longest_processes],
        inter_cluster_edges=[asdict(e) for e in pack.inter_cluster_edges],
        process_count=pack.process_count,
    )


def render_processes(pack: ProcessesFactPack) -> str:
    tmpl = _env().get_template("processes.md.j2")
    return tmpl.render(
        repo=pack.repo_name,
        processes=[
            {"label": p.label, "chain": p.chain, "step_count": p.step_count}
            for p in pack.processes
        ],
        by_cluster={
            cluster: [
                {"label": p.label, "chain": p.chain, "step_count": p.step_count}
                for p in procs
            ]
            for cluster, procs in pack.by_cluster.items()
        },
    )
