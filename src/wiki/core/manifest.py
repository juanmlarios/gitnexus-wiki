"""Phase 1: enumerate the pages this run will produce.

The page set is identical across repos and languages:
    - README.md            (landing page, repo stats + page index)
    - architecture.md      (auto-overview from the graph)
    - processes.md         (every traced execution flow)
    - clusters/<slug>.md   (one per Community, symbolCount > 1)

No project-specific or framework-specific pages live here. Per-project
overrides ship via <project>/.claude/wiki-templates/.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import gitnexus


@dataclass
class Page:
    slug: str
    kind: str  # "readme" | "architecture" | "processes" | "cluster"
    cluster_label: str | None = None
    symbol_count: int = 0


_CLUSTER_QUERY = """
MATCH (c:Community)
WHERE c.symbolCount > 1
RETURN c.heuristicLabel AS label, c.symbolCount AS n
ORDER BY n DESC
"""


def build_pages(repo: str) -> list[Page]:
    pages: list[Page] = [
        Page(slug="README", kind="readme"),
        Page(slug="architecture", kind="architecture"),
        Page(slug="processes", kind="processes"),
    ]
    for p in _build_cluster_pages(repo):
        pages.append(p)
    return pages


def _build_cluster_pages(repo: str) -> list[Page]:
    rows = gitnexus.cypher(repo, _CLUSTER_QUERY)
    pages: list[Page] = []
    seen: set[str] = set()
    for r in rows:
        label = (r.get("label") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        try:
            n = int(r.get("n") or 0)
        except ValueError:
            n = 0
        pages.append(
            Page(
                slug=_slugify(label),
                kind="cluster",
                cluster_label=label,
                symbol_count=n,
            )
        )
    return pages


def _slugify(label: str) -> str:
    return label.lower().replace("_", "-").replace(" ", "-")
