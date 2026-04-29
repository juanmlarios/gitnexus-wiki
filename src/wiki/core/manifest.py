"""Phase 1: enumerate the pages this run will produce."""

from __future__ import annotations

from dataclasses import dataclass

from . import gitnexus


@dataclass
class ClusterPage:
    cluster_label: str
    slug: str
    symbol_count: int


_CLUSTER_QUERY = """
MATCH (c:Community)
WHERE c.symbolCount > 1
RETURN c.heuristicLabel AS label, c.symbolCount AS n
ORDER BY n DESC
"""


def build_cluster_pages(repo: str) -> list[ClusterPage]:
    rows = gitnexus.cypher(repo, _CLUSTER_QUERY)
    pages: list[ClusterPage] = []
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
        pages.append(ClusterPage(cluster_label=label, slug=_slugify(label), symbol_count=n))
    return pages


def _slugify(label: str) -> str:
    return label.lower().replace("_", "-").replace(" ", "-")
