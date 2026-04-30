---
name: gitnexus-wiki
description: Use when the user asks to regenerate, rebuild, or verify the
  docs/wiki/ of any gitnexus-indexed project — examples include "regenerate
  the wiki", "rebuild docs/wiki", "verify the wiki", "check wiki citations",
  "update generated docs after a refactor". Works in any project with a
  .gitnexus/ index.
---

# gitnexus-wiki skill

This skill regenerates a project's `docs/wiki/` from its gitnexus knowledge
graph. Every emitted symbol and path is verified against the graph; failures
are written to `docs/wiki/.failed/` and never to the main wiki.

The engine is a self-contained Python bundle at
`~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki`. It works in any project
with a `.gitnexus/` directory — no per-project setup required.

## Prerequisites

Before invoking, verify:

1. cwd contains a `.gitnexus/` directory (walk up if needed). If not, tell the
   user to run `npx gitnexus analyze` from their project root and stop.
2. `node` / `npx` and `python3 >= 3.10` are on PATH. Fail loudly if not.

## Invocation

Always run from the project root (the directory containing `.gitnexus/`).

| Intent | Command |
|--------|---------|
| Full regen, deterministic only | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki` |
| Full regen with narrative prose (uses local `claude`) | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki --prose` |
| CI / pre-merge gate (never calls LLM) | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki --verify-only` |
| One page | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki --page <slug>` |
| Force regen of cached prose | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki --prose --no-cache` |

The first invocation on a machine creates an isolated venv at
`~/.claude/skills/gitnexus-wiki/.venv/`. Subsequent invocations are instant.

## Reading output

Each page prints `PAGE: <slug>  OK | REJECTED  <reasons>`. If any pages are
rejected:

- They land in `docs/wiki/.failed/`.
- The CLI exits non-zero.
- **Do NOT hand-edit the failed pages.** Failures mean either the template is
  stale (gitnexus schema changed), or the index is stale (run
  `npx gitnexus analyze` first), or a real defect was caught. Tell the user
  which and stop.

## Prose mode (`--prose`)

When the user asks for narrative prose ("make it less robotic", "add descriptions"), pass `--prose`. This:

1. Shells out to the local `claude --print` for each prose slot in the templates.
2. Re-uses the user's existing Claude Code authentication — no API key prompted.
3. Caches results at `.gitnexus-wiki-cache/prose/` per project. Subsequent runs skip the LLM unless the underlying fact pack changed.
4. Falls back to the deterministic body if `claude` is missing, fails, or produces unverified output. The wiki always regenerates.

You should suggest `--prose` proactively when the user complains the wiki feels too dry / table-heavy. Don't suggest it for first-time generation on a new repo (deterministic is faster and cheaper for the initial pass).

## Per-project overrides

If `.claude/wiki.yaml` exists in the project root, the tool reads it for:

```yaml
out_dir: docs/wiki        # default
pages: [storage, ingest]  # filter; omit for all
prose: true               # equivalent to passing --prose
```

## When NOT to use this skill

- The user wants to hand-edit a single wiki page → use Edit, not this skill.
- The user is asking *how* the wiki was generated → answer from this file.
- The project has no `.gitnexus/` directory → tell them to index first.
