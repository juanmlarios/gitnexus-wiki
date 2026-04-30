"""Orchestrator: plan -> build -> verify -> emit."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from . import gitnexus, manifest, render, verify
from .factpack import (
    build_cluster_factpack,
    build_processes_factpack,
    build_repo_factpack,
)
from ..packs import detect_pack


@dataclass
class PageResult:
    slug: str
    status: str  # "ok" | "rejected"
    path: Path
    errors: list[verify.VerifyError]


def generate(
    project_root: Path,
    out_dir: Path,
    *,
    page_filter: list[str] | None = None,
    verify_only: bool = False,
) -> list[PageResult]:
    repo = gitnexus.load_repo(project_root)
    pack = detect_pack(repo, project_root)

    pages = manifest.build_pages(repo.name)
    if page_filter:
        wanted = set(page_filter)
        pages = [
            p
            for p in pages
            if p.slug in wanted or (p.cluster_label and p.cluster_label in wanted)
        ]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clusters").mkdir(parents=True, exist_ok=True)
    failed_dir = out_dir / ".failed"

    # Build the repo-wide fact pack once; README and architecture both consume it.
    repo_fp = None
    if any(p.kind in ("readme", "architecture") for p in pages):
        repo_fp = build_repo_factpack(repo.name, repo.stats, pack=pack)

    cluster_pages = [p for p in pages if p.kind == "cluster"]
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Identifiers that *aren't* symbols but legitimately appear backticked in
    # the universal pages: top-level directory names, the repo name itself.
    extra_known: set[str] = {repo.name}
    if repo_fp:
        extra_known.update(d.path for d in repo_fp.top_level_dirs)
        extra_known.update(c.label for c in repo_fp.communities)

    results: list[PageResult] = []
    for page in pages:
        md = _render_page(repo.name, page, repo_fp, pack, cluster_pages, generated_at)
        target = _target_path(out_dir, page)

        errors = verify.verify_markdown(
            repo.name, md, repo_stats=repo.stats, extra_known=extra_known
        )
        if errors:
            failed_dir.mkdir(parents=True, exist_ok=True)
            failed_path = failed_dir / f"{page.slug}.md"
            if not verify_only:
                failed_path.write_text(md)
            results.append(
                PageResult(slug=page.slug, status="rejected", path=failed_path, errors=errors)
            )
            continue

        if not verify_only:
            target.write_text(md)
        results.append(PageResult(slug=page.slug, status="ok", path=target, errors=[]))

    return results


def _render_page(
    repo_name: str,
    page: manifest.Page,
    repo_fp,
    pack,
    cluster_pages: list[manifest.Page],
    generated_at: str,
) -> str:
    if page.kind == "readme":
        return render.render_readme(repo_fp, cluster_pages, generated_at=generated_at)
    if page.kind == "architecture":
        return render.render_architecture(repo_fp)
    if page.kind == "processes":
        proc_fp = build_processes_factpack(repo_name)
        return render.render_processes(proc_fp)
    if page.kind == "cluster":
        cluster_fp = build_cluster_factpack(repo_name, page.cluster_label)
        pack_extras = pack.cluster_extras(cluster_fp) if pack else None
        return render.render_cluster(cluster_fp, pack_extras=pack_extras)
    raise ValueError(f"Unknown page kind: {page.kind}")


def _target_path(out_dir: Path, page: manifest.Page) -> Path:
    if page.kind == "cluster":
        return out_dir / "clusters" / f"{page.slug}.md"
    if page.kind == "readme":
        return out_dir / "README.md"
    return out_dir / f"{page.slug}.md"
