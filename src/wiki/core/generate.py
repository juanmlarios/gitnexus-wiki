"""Orchestrator: plan -> build -> verify -> emit."""

from __future__ import annotations

import concurrent.futures
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import gitnexus, manifest, prose as _prose, render, verify
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
    prose_meta: dict[str, Any] = field(default_factory=dict)


def generate(
    project_root: Path,
    out_dir: Path,
    *,
    page_filter: list[str] | None = None,
    verify_only: bool = False,
    prose: bool = False,
    model: str = "sonnet",
    use_cache: bool = True,
    prose_workers: int = 6,
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

    repo_fp = None
    if any(p.kind in ("readme", "architecture") for p in pages):
        repo_fp = build_repo_factpack(repo.name, repo.stats, pack=pack)

    cluster_pages = [p for p in pages if p.kind == "cluster"]
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    extra_known: set[str] = {repo.name}
    if repo_fp:
        extra_known.update(d.path for d in repo_fp.top_level_dirs)
        extra_known.update(c.label for c in repo_fp.communities)

    def build_one(page: manifest.Page) -> PageResult:
        meta = _ProseMeta()
        handler = _BatchedProseHandler(
            project_root=project_root,
            enabled=prose,
            model=model,
            use_cache=use_cache,
            meta=meta,
        )
        md = _render_page(
            repo.name, page, repo_fp, pack, cluster_pages, generated_at, handler.jinja_handler
        )
        md = handler.fill(md)
        target = _target_path(out_dir, page)
        errors = verify.verify_markdown(
            repo.name, md, repo_stats=repo.stats, extra_known=extra_known
        )
        if errors:
            failed_dir.mkdir(parents=True, exist_ok=True)
            failed_path = failed_dir / f"{page.slug}.md"
            if not verify_only:
                failed_path.write_text(md)
            return PageResult(
                slug=page.slug,
                status="rejected",
                path=failed_path,
                errors=errors,
                prose_meta=meta.snapshot(),
            )
        if not verify_only:
            target.write_text(md)
        return PageResult(
            slug=page.slug, status="ok", path=target, errors=[], prose_meta=meta.snapshot()
        )

    results: list[PageResult] = [None] * len(pages)  # type: ignore[list-item]
    if prose and prose_workers > 1 and len(pages) > 1:
        # When prose is on, each page may make several blocking subprocess
        # calls — parallelize page builds so wall time scales with the slowest
        # page, not the sum.
        with concurrent.futures.ThreadPoolExecutor(max_workers=prose_workers) as ex:
            future_to_idx = {ex.submit(build_one, page): i for i, page in enumerate(pages)}
            for fut in concurrent.futures.as_completed(future_to_idx):
                results[future_to_idx[fut]] = fut.result()
    else:
        for i, page in enumerate(pages):
            results[i] = build_one(page)

    return results


# ---- prose handler factory --------------------------------------------------


@dataclass
class _ProseMeta:
    fresh: int = 0
    cache_hits: int = 0
    fallbacks: int = 0
    fallback_slots: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "fresh": self.fresh,
            "cache_hits": self.cache_hits,
            "fallbacks": self.fallbacks,
            "fallback_slots": list(self.fallback_slots),
        }


class _BatchedProseHandler:
    """Two-pass prose handler.

    Pass 1 (during Jinja render): record each {% prose %} block as a slot
    and substitute a unique placeholder. The fact_pack is captured from the
    first slot encountered (it's the same per page).

    Pass 2 (after render): make ONE LLM call for the whole page, parse the
    JSON response, and substitute placeholders in the rendered markdown.
    Slots that the LLM omitted, marked GAP, or failed to verify keep their
    deterministic fallback.
    """

    def __init__(self, *, project_root: Path, enabled: bool, model: str,
                 use_cache: bool, meta: "_ProseMeta"):
        self.project_root = project_root
        self.enabled = enabled
        self.model = model
        self.use_cache = use_cache
        self.meta = meta
        self._slots: list[tuple[str, str, str]] = []  # (slot_name, fallback, placeholder)
        self._fact_pack: dict | None = None

    @property
    def jinja_handler(self) -> Callable | None:
        if not self.enabled:
            return None
        return self._collect

    def _collect(self, *, slot_name: str, fallback: str, fact_pack: dict) -> str:
        if self._fact_pack is None:
            self._fact_pack = fact_pack
        idx = len(self._slots)
        placeholder = f"PROSE:{slot_name}:{idx}"
        self._slots.append((slot_name, fallback, placeholder))
        return placeholder

    def fill(self, rendered_md: str) -> str:
        if not self.enabled or not self._slots:
            return rendered_md

        # Build the spec list for known slots; unknown slots keep their fallback.
        known_specs: list[_prose.ProseSlot] = []
        fallbacks: dict[str, str] = {}
        for slot_name, fallback, _ in self._slots:
            spec = _prose.SLOT_SPECS.get(slot_name)
            if spec is None:
                continue
            if spec.name not in fallbacks:  # de-dupe; same slot may appear twice
                known_specs.append(spec)
                fallbacks[spec.name] = fallback

        result = _prose.generate_batched_prose(
            slot_specs=known_specs,
            fallbacks=fallbacks,
            fact_pack=self._fact_pack or {},
            project_root=self.project_root,
            model=self.model,
            use_cache=self.use_cache,
        )

        if result.cached:
            self.meta.cache_hits += len(known_specs)
        else:
            self.meta.fresh += len(known_specs) - len(result.fallback_slots)
            self.meta.fallbacks += len(result.fallback_slots)
            self.meta.fallback_slots.extend(result.fallback_slots)

        # Substitute placeholders.
        for slot_name, fallback, placeholder in self._slots:
            text = result.texts.get(slot_name, fallback)
            rendered_md = rendered_md.replace(placeholder, text)
        return rendered_md


# ---- per-page dispatch ------------------------------------------------------


def _render_page(
    repo_name: str,
    page: manifest.Page,
    repo_fp,
    pack,
    cluster_pages: list[manifest.Page],
    generated_at: str,
    prose_handler: Callable | None,
) -> str:
    if page.kind == "readme":
        return render.render_readme(
            repo_fp, cluster_pages, generated_at=generated_at, prose_handler=prose_handler
        )
    if page.kind == "architecture":
        return render.render_architecture(repo_fp, prose_handler=prose_handler)
    if page.kind == "processes":
        proc_fp = build_processes_factpack(repo_name)
        return render.render_processes(proc_fp, prose_handler=prose_handler)
    if page.kind == "cluster":
        cluster_fp = build_cluster_factpack(repo_name, page.cluster_label)
        pack_extras = pack.cluster_extras(cluster_fp) if pack else None
        return render.render_cluster(
            cluster_fp, pack_extras=pack_extras, prose_handler=prose_handler
        )
    raise ValueError(f"Unknown page kind: {page.kind}")


def _target_path(out_dir: Path, page: manifest.Page) -> Path:
    if page.kind == "cluster":
        return out_dir / "clusters" / f"{page.slug}.md"
    if page.kind == "readme":
        return out_dir / "README.md"
    return out_dir / f"{page.slug}.md"
