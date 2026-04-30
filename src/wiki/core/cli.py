"""CLI entrypoint."""

from __future__ import annotations

import argparse
import logging
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
        help="Run the citation linter without writing files. Never calls the LLM.",
    )
    parser.add_argument(
        "--prose",
        action="store_true",
        help=(
            "Replace deterministic prose blocks with bounded LLM output via the "
            "local `claude` CLI. Output is verified the same as everything else; "
            "unverified output falls back to the deterministic body."
        ),
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="LLM model alias for --prose (default: sonnet). Ignored without --prose.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force regen of all prose slots, ignoring the cache.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

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
            prose=args.prose,
            model=args.model,
            use_cache=not args.no_cache,
        )
    except gitnexus.GitnexusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    failed = 0
    fallback_slots: list[tuple[str, str]] = []
    for r in results:
        if r.status == "ok":
            extra = ""
            if r.prose_meta:
                hits = r.prose_meta.get("cache_hits", 0)
                fb = r.prose_meta.get("fallbacks", 0)
                fresh = r.prose_meta.get("fresh", 0)
                extra = f"  [prose: {fresh} new, {hits} cached, {fb} fallback]"
                for slot in r.prose_meta.get("fallback_slots", []):
                    fallback_slots.append((r.slug, slot))
            print(
                f"PAGE: {r.slug:<24} OK         -> "
                f"{_safe_relpath(r.path, project_root)}{extra}"
            )
        elif r.status == "rejected":
            failed += 1
            reasons = ", ".join(f"{e.kind}={e.value}" for e in r.errors)
            print(f"PAGE: {r.slug:<24} REJECTED   {reasons}")
        else:
            print(f"PAGE: {r.slug:<24} {r.status}")

    if fallback_slots:
        print(
            f"\nNote: {len(fallback_slots)} prose slot(s) used the deterministic fallback. "
            "Run with -v for details, or check that `claude` is on PATH and authenticated.",
            file=sys.stderr,
        )

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
