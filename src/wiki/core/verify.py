"""Phase 3: cite-and-verify lint pass.

Every backticked path and symbol-shaped identifier in the rendered markdown
must round-trip through the gitnexus index. Anything that doesn't is a defect.

Implementation note: gitnexus's full-text index on `name` is fragile when the
DB is read-only (e.g. when an MCP server is attached). We avoid that path
entirely by fetching the universe of paths and names once per run and checking
membership in Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import gitnexus

_PATH_RE = re.compile(
    r"`([\w./\-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|cs|cpp|c|h|hpp|yaml|yml|toml|json|md))`"
)
_IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})`")
_GAP_RE = re.compile(r"\[GAP:[^\]]*\]")

_SYMBOL_KINDS = ["Function", "Class", "Method", "Interface"]

_COMMON_WORDS = {
    "true", "false", "null", "None", "yes", "no", "default", "auto",
    "config", "yaml", "json", "csv", "parquet", "jsonl",
    # Cypher relationship types that templates may name in prose:
    "CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS", "CONTAINS", "DEFINES",
    "MEMBER_OF", "STEP_IN_PROCESS", "HAS_METHOD", "HAS_PROPERTY",
    "ACCESSES", "METHOD_OVERRIDES", "METHOD_IMPLEMENTS",
    # Tool / CLI names that show up in template footers:
    "claude", "gitnexus", "npx", "git", "python", "pip", "uv",
}


@dataclass
class VerifyError:
    kind: str  # "missing_path" | "missing_symbol" | "gap"
    value: str
    detail: str = ""


# Cache the universe of paths/names per repo for the lifetime of one run.
_path_cache: dict[str, set[str]] = {}
_name_cache: dict[str, set[str]] = {}


def _known_paths(repo: str) -> set[str]:
    if repo not in _path_cache:
        rows = gitnexus.cypher(repo, "MATCH (f:File) RETURN f.filePath AS path")
        _path_cache[repo] = {r["path"] for r in rows if r.get("path")}
    return _path_cache[repo]


def _known_names(repo: str) -> set[str]:
    if repo not in _name_cache:
        names: set[str] = set()
        for kind in _SYMBOL_KINDS:
            rows = gitnexus.cypher(repo, f"MATCH (n:{kind}) RETURN n.name AS name")
            names.update(r["name"] for r in rows if r.get("name"))
        # Communities also surface as backticked labels in some templates.
        rows = gitnexus.cypher(repo, "MATCH (c:Community) RETURN c.heuristicLabel AS name")
        names.update(r["name"] for r in rows if r.get("name"))
        _name_cache[repo] = names
    return _name_cache[repo]


def reset_caches() -> None:
    _path_cache.clear()
    _name_cache.clear()


def verify_markdown(
    repo: str,
    md: str,
    *,
    repo_stats: dict | None = None,
    extra_known: set[str] | None = None,
) -> list[VerifyError]:
    """Lint a rendered page.

    extra_known: identifiers that the caller can prove are legitimate even though
    they're not in the symbol graph (e.g. top-level directory names, the repo name).
    """
    errors: list[VerifyError] = []
    extra_known = extra_known or set()

    for m in _GAP_RE.findall(md):
        errors.append(VerifyError(kind="gap", value=m))

    paths = sorted(set(_PATH_RE.findall(md)))
    if paths:
        known_paths = _known_paths(repo)
        for p in paths:
            if p not in known_paths:
                errors.append(VerifyError(kind="missing_path", value=p))

    idents = set(_IDENT_RE.findall(md))
    # Drop tokens that already appeared inside a backticked path
    # (so things like `local_fs` from `src/.../local_fs.py` don't get re-checked).
    path_tokens: set[str] = set()
    for p in paths:
        path_tokens.update(p.replace("/", " ").replace(".", " ").split())
    idents -= path_tokens
    idents -= _COMMON_WORDS
    idents -= extra_known
    if idents:
        known_names = _known_names(repo)
        for ident in sorted(idents):
            if ident not in known_names:
                errors.append(VerifyError(kind="missing_symbol", value=ident))

    return errors
