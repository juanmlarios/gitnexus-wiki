"""Orchestrator: plan -> build -> verify -> emit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import gitnexus, manifest, render, verify
from .factpack import build_cluster_factpack
from ..packs import detect_pack


@dataclass
class PageResult:
    slug: str
    status: str  # "ok" | "rejected" | "skipped"
    path: Path | None
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

    pages = manifest.build_cluster_pages(repo.name)
    if page_filter:
        pages = [p for p in pages if p.slug in page_filter or p.cluster_label in page_filter]

    results: list[PageResult] = []
    clusters_dir = out_dir / "clusters"
    failed_dir = out_dir / ".failed"
    clusters_dir.mkdir(parents=True, exist_ok=True)

    for page in pages:
        factpack = build_cluster_factpack(repo.name, page.cluster_label)
        pack_extras = pack.cluster_extras(factpack) if pack else None
        md = render.render_cluster(factpack, pack_extras=pack_extras)

        errors = verify.verify_markdown(repo.name, md, repo_stats=repo.stats)

        target = clusters_dir / f"{page.slug}.md"
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
