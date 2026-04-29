"""CLI entrypoint."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import gitnexus, generate as _gen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gitnexus-wiki", description=__doc__)
    parser.add_argument(
        "--out-dir",
        default="docs/wiki",
        help="Output directory relative to project root (default: docs/wiki).",
    )
    parser.add_argument(
        "--page",
        action="append",
        default=None,
        help="Only generate this page (slug or cluster label). Repeat for multiple.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Run the citation linter without writing files.",
    )
    parser.add_argument(
        "--no-prose",
        action="store_true",
        help="Skip optional LLM-filled prose blocks (deterministic skeleton only).",
    )
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    try:
        project_root = gitnexus.find_project_root(cwd)
    except gitnexus.GitnexusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    out_dir = project_root / args.out_dir

    try:
        results = _gen.generate(
            project_root=project_root,
            out_dir=out_dir,
            page_filter=args.page,
            verify_only=args.verify_only,
        )
    except gitnexus.GitnexusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    failed = 0
    for r in results:
        if r.status == "ok":
            print(f"PAGE: {r.slug:<24} OK         -> {_safe_relpath(r.path, project_root)}")
        elif r.status == "rejected":
            failed += 1
            reasons = ", ".join(f"{e.kind}={e.value}" for e in r.errors)
            print(f"PAGE: {r.slug:<24} REJECTED   {reasons}")
        else:
            print(f"PAGE: {r.slug:<24} {r.status}")

    if failed:
        print(f"\n{failed} page(s) rejected. See {out_dir / '.failed'} for output.", file=sys.stderr)
        return 1
    return 0


def _safe_relpath(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


if __name__ == "__main__":
    sys.exit(main())
