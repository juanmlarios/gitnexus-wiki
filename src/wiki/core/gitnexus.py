"""Thin wrapper over `npx gitnexus` CLI. The only place that shells out."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitnexusError(RuntimeError):
    pass


@dataclass
class Repo:
    name: str
    path: Path
    stats: dict


def find_project_root(start: Path) -> Path:
    """Walk up from `start` until we find a `.gitnexus/` directory."""
    for d in [start, *start.parents]:
        if (d / ".gitnexus").is_dir():
            return d
    raise GitnexusError(
        f"Not inside a gitnexus-indexed project (no .gitnexus/ found above {start}). "
        "Run `npx gitnexus analyze` from the project root first."
    )


def _run_cli(args: list[str]) -> str:
    if shutil.which("npx") is None:
        raise GitnexusError("`npx` not found on PATH; install Node.js to use the gitnexus CLI.")
    cmd = ["npx", "--no-install", "gitnexus", *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise GitnexusError(f"gitnexus CLI failed: {' '.join(args)}\n{res.stderr.strip()}")
    return res.stdout


def load_repo(project_root: Path) -> Repo:
    """Read .gitnexus/meta.json for the repo name and stats."""
    meta_path = project_root / ".gitnexus" / "meta.json"
    if not meta_path.exists():
        raise GitnexusError(f"Missing {meta_path}. Run `npx gitnexus analyze` first.")
    meta = json.loads(meta_path.read_text())
    # Use the directory name as the repo label gitnexus uses; fall back to repoPath basename.
    name = Path(meta.get("repoPath", project_root)).name
    return Repo(name=name, path=project_root, stats=meta.get("stats", {}))


def cypher(repo: str, query: str) -> list[dict]:
    """Run a Cypher query and return a list of row dicts."""
    out = _run_cli(["cypher", "--repo", repo, query])
    payload = json.loads(out)
    # The CLI returns one of:
    #   {"markdown": "...", "row_count": N}  on success
    #   {"error": "..."}                     on error
    #   []                                   on zero rows
    if isinstance(payload, list):
        return []
    if "error" in payload:
        raise GitnexusError(f"Cypher error: {payload['error']}\nQuery: {query}")
    return _parse_markdown_table(payload.get("markdown", ""))


def context(repo: str, name: str, *, content: bool = False, file_path: str | None = None) -> dict:
    """Return the JSON `context` payload for a symbol."""
    args = ["context", name, "--repo", repo]
    if content:
        args.append("--content")
    if file_path:
        args.extend(["--file", file_path])
    out = _run_cli(args)
    return json.loads(out)


# ---- markdown table parsing -------------------------------------------------

_TABLE_HEADER_RE = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


def _parse_markdown_table(md: str) -> list[dict]:
    if not md.strip():
        return []
    lines = [ln for ln in md.splitlines() if ln.strip()]
    if len(lines) < 2 or not _TABLE_HEADER_RE.match(lines[0]) or not _TABLE_SEP_RE.match(lines[1]):
        return []
    headers = [c.strip() for c in lines[0].strip("|").split("|")]
    rows: list[dict] = []
    for ln in lines[2:]:
        if not _TABLE_HEADER_RE.match(ln):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows
