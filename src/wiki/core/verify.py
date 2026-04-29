"""Phase 3: cite-and-verify lint pass.

Every backticked path and symbol-shaped identifier in the rendered markdown
must round-trip through the gitnexus index. Anything that doesn't is a defect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import gitnexus

# A backticked filename: anything ending in a recognised source/config extension.
_PATH_RE = re.compile(
    r"`([\w./\-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|cs|cpp|c|h|hpp|yaml|yml|toml|json|md))`"
)
# A backticked identifier: snake_case, CamelCase, or snake_with_underscore.
_IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})`")
_GAP_RE = re.compile(r"\[GAP:[^\]]*\]")


@dataclass
class VerifyError:
    kind: str  # "missing_path" | "missing_symbol" | "gap" | "stale_stat"
    value: str
    detail: str = ""


def verify_markdown(repo: str, md: str, *, repo_stats: dict | None = None) -> list[VerifyError]:
    errors: list[VerifyError] = []

    # 1) GAP markers from prose-fill blocks
    for m in _GAP_RE.findall(md):
        errors.append(VerifyError(kind="gap", value=m))

    # 2) Path citations
    paths = sorted(set(_PATH_RE.findall(md)))
    if paths:
        rows = gitnexus.cypher(
            repo,
            "MATCH (f:File) WHERE f.filePath IN ["
            + ", ".join(f"'{p}'" for p in paths)
            + "] RETURN f.filePath",
        )
        present = {r.get("f.filePath") for r in rows}
        for p in paths:
            if p not in present:
                errors.append(VerifyError(kind="missing_path", value=p))

    # 3) Symbol citations
    idents = set(_IDENT_RE.findall(md))
    # Drop anything that already appeared as a path's basename or extension.
    path_parts: set[str] = set()
    for p in paths:
        path_parts.update(p.replace("/", " ").replace(".", " ").split())
    idents -= path_parts
    # Drop common prose words.
    idents -= _COMMON_WORDS
    if idents:
        rows = gitnexus.cypher(
            repo,
            "MATCH (n) WHERE n.name IN ["
            + ", ".join(f"'{i}'" for i in sorted(idents))
            + "] RETURN DISTINCT n.name",
        )
        present_names = {r.get("n.name") for r in rows}
        for ident in sorted(idents):
            if ident not in present_names:
                errors.append(VerifyError(kind="missing_symbol", value=ident))

    return errors


# Words that show up backticked in prose but aren't symbols.
_COMMON_WORDS = {
    "true",
    "false",
    "null",
    "None",
    "yes",
    "no",
    "default",
    "auto",
    "config",
    "yaml",
    "json",
    "csv",
    "parquet",
    "jsonl",
    "id_column",
    "scenario_id",
}
