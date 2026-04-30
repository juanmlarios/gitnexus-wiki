"""Phase 2a: build per-page fact bundles from the gitnexus graph.

A fact pack is the *only* thing the LLM (if used) ever sees. Every entry comes
straight from a Cypher query — no prose, no extrapolation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import gitnexus


# ---- shared shapes ----------------------------------------------------------


@dataclass
class FilePack:
    path: str
    symbols: list[dict] = field(default_factory=list)


@dataclass
class ClassPack:
    name: str
    file_path: str
    base_line: str = ""  # first source line, e.g. "class Foo(TypedDict):"


@dataclass
class ProcessPack:
    label: str
    chain: list[str]
    step_count: int


# ---- per-page fact packs ----------------------------------------------------


@dataclass
class ClusterFactPack:
    cluster_label: str
    files: list[FilePack]
    classes: list[ClassPack]
    processes: list[ProcessPack]
    stats: dict[str, Any]


@dataclass
class TopLevelDir:
    path: str
    file_count: int
    symbol_count: int
    pct_of_codebase: float


@dataclass
class CommunityStats:
    label: str
    slug: str
    symbol_count: int
    cohesion: float
    largest_file: str
    largest_file_count: int


@dataclass
class TypeSurfaceEntry:
    base_label: str  # "TypedDict" / "BaseModel" / "Protocol" / "(no base)" / "extends Foo"
    count: int
    examples: list[str]


@dataclass
class CrossClusterEdge:
    from_cluster: str
    to_cluster: str
    call_count: int


@dataclass
class RepoFactPack:
    repo_name: str
    stats: dict[str, Any]
    top_level_dirs: list[TopLevelDir]
    communities: list[CommunityStats]
    classes: list[ClassPack]
    type_surface: list[TypeSurfaceEntry]
    longest_processes: list[ProcessPack]
    inter_cluster_edges: list[CrossClusterEdge]
    process_count: int


@dataclass
class ProcessesFactPack:
    repo_name: str
    processes: list[ProcessPack]
    by_cluster: dict[str, list[ProcessPack]]


# ---- queries (use __TOKEN__ placeholders to avoid Cypher {} clash) ----------


_FILES_IN_CLUSTER_QUERY = """
MATCH (s)-[:CodeRelation {type: 'MEMBER_OF'}]->(c:Community)
WHERE c.heuristicLabel = '__CLUSTER__'
WITH DISTINCT s.filePath AS path
MATCH (n) WHERE n.filePath = path
RETURN DISTINCT path ORDER BY path
"""

_SYMBOL_KINDS = ["Function", "Class", "Method", "Interface"]


def _symbols_query_for(kind: str) -> str:
    return (
        f"MATCH (n:{kind}) WHERE n.filePath = '__PATH__' "
        f"RETURN '{kind}' AS kind, n.name AS name ORDER BY name"
    )


_PROCESSES_TOUCHING_FILES_QUERY = """
MATCH (s)-[r:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process)
WHERE s.filePath IN [__FILE_LIST__]
RETURN p.heuristicLabel AS label, s.name AS step_name, r.step AS step
ORDER BY label, step
"""

_ALL_FILES_QUERY = """
MATCH (f:File) RETURN f.filePath AS path
"""

_ALL_CLASSES_QUERY = """
MATCH (c:Class) RETURN c.name AS name, c.filePath AS path ORDER BY c.filePath, c.name
"""

_ALL_COMMUNITIES_QUERY = """
MATCH (c:Community)
WHERE c.symbolCount > 1
RETURN c.heuristicLabel AS label, c.symbolCount AS n, c.cohesion AS cohesion
ORDER BY c.symbolCount DESC
"""

_LARGEST_FILE_IN_COMMUNITY_QUERY = """
MATCH (s)-[:CodeRelation {type: 'MEMBER_OF'}]->(c:Community)
WHERE c.heuristicLabel = '__LABEL__'
RETURN s.filePath AS path, count(s) AS n
ORDER BY n DESC
LIMIT 1
"""

_ALL_PROCESSES_QUERY = """
MATCH (s)-[r:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process)
RETURN p.heuristicLabel AS label, s.name AS step_name, r.step AS step
ORDER BY label, step
"""

_INTER_CLUSTER_CALLS_QUERY = """
MATCH (a)-[r:CodeRelation {type: 'CALLS'}]->(b)
MATCH (a)-[:CodeRelation {type: 'MEMBER_OF'}]->(ca:Community)
MATCH (b)-[:CodeRelation {type: 'MEMBER_OF'}]->(cb:Community)
WHERE ca.heuristicLabel <> cb.heuristicLabel
RETURN ca.heuristicLabel AS from_cluster, cb.heuristicLabel AS to_cluster, count(*) AS n
ORDER BY n DESC
LIMIT 20
"""


# ---- helpers ----------------------------------------------------------------


def _quoted_list(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


def _slugify(label: str) -> str:
    return label.lower().replace("_", "-").replace(" ", "-")


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---- builders ---------------------------------------------------------------


def build_cluster_factpack(repo: str, cluster: str) -> ClusterFactPack:
    file_paths = [
        row["path"]
        for row in gitnexus.cypher(
            repo, _FILES_IN_CLUSTER_QUERY.replace("__CLUSTER__", cluster)
        )
        if row.get("path")
    ]

    files: list[FilePack] = []
    classes: list[ClassPack] = []
    for path in file_paths:
        symbols: list[dict] = []
        for kind in _SYMBOL_KINDS:
            rows = gitnexus.cypher(repo, _symbols_query_for(kind).replace("__PATH__", path))
            for r in rows:
                if r.get("name"):
                    symbols.append({"kind": kind, "name": r["name"]})
        files.append(FilePack(path=path, symbols=symbols))

        for sym in symbols:
            if sym["kind"] != "Class":
                continue
            ctx = gitnexus.context(repo, sym["name"], content=True, file_path=path)
            if ctx.get("status") == "found":
                src = ctx.get("symbol", {}).get("content", "")
                base_line = next(
                    (ln.strip() for ln in src.splitlines() if ln.strip().startswith("class ")),
                    "",
                )
                classes.append(ClassPack(name=sym["name"], file_path=path, base_line=base_line))

    processes: list[ProcessPack] = []
    if file_paths:
        rows = gitnexus.cypher(
            repo,
            _PROCESSES_TOUCHING_FILES_QUERY.replace("__FILE_LIST__", _quoted_list(file_paths)),
        )
        by_label: dict[str, list[str]] = {}
        for r in rows:
            label = r.get("label") or ""
            step_name = r.get("step_name") or ""
            if not label or not step_name:
                continue
            by_label.setdefault(label, []).append(step_name)
        for label, chain in sorted(by_label.items()):
            processes.append(ProcessPack(label=label, chain=chain, step_count=len(chain)))

    stats = {
        "file_count": len(files),
        "symbol_count": sum(len(f.symbols) for f in files),
        "process_count": len(processes),
        "class_count": len(classes),
    }
    return ClusterFactPack(
        cluster_label=cluster,
        files=files,
        classes=classes,
        processes=processes,
        stats=stats,
    )


def build_repo_factpack(repo: str, repo_stats: dict, *, pack=None) -> RepoFactPack:
    """Build the universal fact pack used by README and architecture pages."""
    # 1) Top-level dirs from File paths.
    file_rows = gitnexus.cypher(repo, _ALL_FILES_QUERY)
    file_paths = [r["path"] for r in file_rows if r.get("path")]
    by_top_files: dict[str, int] = defaultdict(int)
    for p in file_paths:
        head = p.split("/", 1)[0] if "/" in p else "."
        by_top_files[head] += 1

    # Pull every symbol path once per label and aggregate in Python.
    by_top_symbols: dict[str, int] = defaultdict(int)
    for kind in _SYMBOL_KINDS:
        rows = gitnexus.cypher(repo, f"MATCH (n:{kind}) RETURN n.filePath AS path")
        for r in rows:
            p = r.get("path") or ""
            if not p:
                continue
            head = p.split("/", 1)[0] if "/" in p else "."
            by_top_symbols[head] += 1

    total_symbols = sum(by_top_symbols.values())
    top_level_data = sorted(
        (
            (head, by_top_files[head], by_top_symbols.get(head, 0))
            for head in by_top_files
        ),
        key=lambda r: (-r[2], -r[1], r[0]),
    )
    top_level_dirs = [
        TopLevelDir(
            path=p,
            file_count=fc,
            symbol_count=sc,
            pct_of_codebase=(100.0 * sc / total_symbols) if total_symbols else 0.0,
        )
        for p, fc, sc in top_level_data
    ]

    # 2) Communities + their largest file.
    community_rows = gitnexus.cypher(repo, _ALL_COMMUNITIES_QUERY)
    communities: list[CommunityStats] = []
    for r in community_rows:
        label = (r.get("label") or "").strip()
        if not label:
            continue
        n = _safe_int(r.get("n"))
        cohesion = _safe_float(r.get("cohesion"))
        biggest = gitnexus.cypher(
            repo, _LARGEST_FILE_IN_COMMUNITY_QUERY.replace("__LABEL__", label)
        )
        if biggest:
            largest_file = biggest[0].get("path", "")
            largest_count = _safe_int(biggest[0].get("n"))
        else:
            largest_file = ""
            largest_count = 0
        communities.append(
            CommunityStats(
                label=label,
                slug=_slugify(label),
                symbol_count=n,
                cohesion=cohesion,
                largest_file=largest_file,
                largest_file_count=largest_count,
            )
        )

    # 3) All classes with first source line (for type surface).
    class_rows = gitnexus.cypher(repo, _ALL_CLASSES_QUERY)
    classes: list[ClassPack] = []
    for r in class_rows:
        name = r.get("name") or ""
        path = r.get("path") or ""
        if not name or not path:
            continue
        ctx = gitnexus.context(repo, name, content=True, file_path=path)
        if ctx.get("status") != "found":
            continue
        src = ctx.get("symbol", {}).get("content", "")
        base_line = next(
            (ln.strip() for ln in src.splitlines() if ln.strip().startswith("class ")),
            "",
        )
        classes.append(ClassPack(name=name, file_path=path, base_line=base_line))

    # 4) Type surface: group classes by detected base label.
    type_surface = _build_type_surface(classes, pack=pack)

    # 5) Longest processes (top 10 by step count).
    proc_rows = gitnexus.cypher(repo, _ALL_PROCESSES_QUERY)
    by_label_proc: dict[str, list[str]] = {}
    for r in proc_rows:
        lbl = r.get("label") or ""
        step_name = r.get("step_name") or ""
        if lbl and step_name:
            by_label_proc.setdefault(lbl, []).append(step_name)
    all_processes = [
        ProcessPack(label=lbl, chain=chain, step_count=len(chain))
        for lbl, chain in by_label_proc.items()
    ]
    all_processes.sort(key=lambda p: (-p.step_count, p.label))
    longest_processes = all_processes[:10]

    # 6) Inter-cluster CALLS edges.
    icc_rows = gitnexus.cypher(repo, _INTER_CLUSTER_CALLS_QUERY)
    inter_cluster_edges = [
        CrossClusterEdge(
            from_cluster=r.get("from_cluster", ""),
            to_cluster=r.get("to_cluster", ""),
            call_count=_safe_int(r.get("n")),
        )
        for r in icc_rows
        if r.get("from_cluster") and r.get("to_cluster")
    ]

    return RepoFactPack(
        repo_name=repo,
        stats=repo_stats,
        top_level_dirs=top_level_dirs,
        communities=communities,
        classes=classes,
        type_surface=type_surface,
        longest_processes=longest_processes,
        inter_cluster_edges=inter_cluster_edges,
        process_count=len(all_processes),
    )


def build_processes_factpack(repo: str) -> ProcessesFactPack:
    rows = gitnexus.cypher(repo, _ALL_PROCESSES_QUERY)
    by_label: dict[str, list[str]] = {}
    for r in rows:
        lbl = r.get("label") or ""
        step_name = r.get("step_name") or ""
        if lbl and step_name:
            by_label.setdefault(lbl, []).append(step_name)
    processes = [
        ProcessPack(label=lbl, chain=chain, step_count=len(chain))
        for lbl, chain in by_label.items()
    ]
    processes.sort(key=lambda p: (-p.step_count, p.label))

    # Group processes by the community of their first step. Avoid the
    # `WHERE n.name = ...` predicate (FTS-fragile in read-only mode); pull
    # the (name -> cluster) map once with label-only queries and look up
    # in Python.
    name_to_cluster: dict[str, str] = {}
    for kind in _SYMBOL_KINDS:
        rows = gitnexus.cypher(
            repo,
            f"MATCH (n:{kind})-[:CodeRelation {{type: 'MEMBER_OF'}}]->(c:Community) "
            "RETURN n.name AS name, c.heuristicLabel AS label",
        )
        for r in rows:
            n, lbl = r.get("name"), r.get("label")
            if n and lbl:
                name_to_cluster.setdefault(n, lbl)

    by_cluster: dict[str, list[ProcessPack]] = defaultdict(list)
    for proc in processes:
        first = proc.chain[0] if proc.chain else None
        cluster = name_to_cluster.get(first or "", "(unclustered)")
        by_cluster[cluster].append(proc)

    return ProcessesFactPack(repo_name=repo, processes=processes, by_cluster=dict(by_cluster))


# ---- type-surface aggregation -----------------------------------------------


def _build_type_surface(classes: list[ClassPack], *, pack) -> list[TypeSurfaceEntry]:
    """Group classes by base label using the provided pack's discriminator
    (or a generic fallback that just reports `class Foo(Bar)` → 'extends Bar')."""
    label_for_line = (
        pack.label_for_class_line
        if pack is not None and hasattr(pack, "label_for_class_line")
        else _generic_label_for_class_line
    )

    buckets: dict[str, list[str]] = defaultdict(list)
    for c in classes:
        label = label_for_line(c.base_line) or "(no base)"
        buckets[label].append(c.name)

    out: list[TypeSurfaceEntry] = []
    for label, names in buckets.items():
        out.append(TypeSurfaceEntry(base_label=label, count=len(names), examples=names[:3]))
    out.sort(key=lambda e: (-e.count, e.base_label))
    return out


def _generic_label_for_class_line(line: str) -> str | None:
    """Fallback discriminator: report 'extends Foo' or '(no base)' only."""
    import re

    if not line:
        return None
    m = re.match(r"^\s*class\s+\w+\(([^)]*)\)\s*:", line)
    if not m:
        return None
    bases = [b.strip() for b in m.group(1).split(",") if b.strip()]
    if not bases:
        return None
    return f"extends {bases[0].split('[', 1)[0]}"
