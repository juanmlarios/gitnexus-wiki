"""Phase 2a: build per-page fact bundles from the gitnexus graph.

A fact pack is the *only* thing the LLM (if used) ever sees. Every entry comes
straight from a Cypher query — no prose, no extrapolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import gitnexus


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


@dataclass
class ClusterFactPack:
    cluster_label: str
    files: list[FilePack]
    classes: list[ClassPack]
    processes: list[ProcessPack]
    stats: dict[str, Any]


# ---- queries ----------------------------------------------------------------

_FILES_IN_CLUSTER_QUERY = """
MATCH (s)-[:CodeRelation {type: 'MEMBER_OF'}]->(c:Community)
WHERE c.heuristicLabel = '__CLUSTER__'
WITH DISTINCT s.filePath AS path
MATCH (n) WHERE n.filePath = path
RETURN DISTINCT path ORDER BY path
"""

# labels(n)[0] returns empty in some Kuzu builds; query each node type explicitly.
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


def _quoted_list(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


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

        # Pull the first source line for each class so packs can discriminate
        # idioms (TypedDict / BaseModel / Protocol / ...).
        for sym in symbols:
            if sym["kind"] == "Class" or _looks_like_class(sym["name"]):
                ctx = gitnexus.context(repo, sym["name"], content=True, file_path=path)
                if ctx.get("status") == "found":
                    src = ctx.get("symbol", {}).get("content", "")
                    base_line = next(
                        (ln.strip() for ln in src.splitlines() if ln.strip().startswith("class ")),
                        "",
                    )
                    classes.append(
                        ClassPack(name=sym["name"], file_path=path, base_line=base_line)
                    )

    processes: list[ProcessPack] = []
    if file_paths:
        rows = gitnexus.cypher(
            repo,
            _PROCESSES_TOUCHING_FILES_QUERY.replace("__FILE_LIST__", _quoted_list(file_paths)),
        )
        # Group rows by process label, preserving step order.
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


def _looks_like_class(name: str) -> bool:
    return bool(name) and name[0].isupper() and "_" not in name


def _parse_kuzu_list(s: str) -> list[str]:
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [item.strip().strip("'\"") for item in s.split(",") if item.strip()]
