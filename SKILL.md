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
| Full regen | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki` |
| CI / pre-merge gate | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki --verify-only` |
| One page | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki --page <slug>` |
| Skeleton only (no prose) | `~/.claude/skills/gitnexus-wiki/bin/gitnexus-wiki --no-prose` |

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

## Per-project overrides

If `.claude/wiki.yaml` exists in the project root, the tool reads it for:

```yaml
out_dir: docs/wiki        # default
pages: [storage, ingest]  # filter; omit for all
prose: true               # fill prose blocks (requires LLM access)
```

## When NOT to use this skill

- The user wants to hand-edit a single wiki page → use Edit, not this skill.
- The user is asking *how* the wiki was generated → answer from this file.
- The project has no `.gitnexus/` directory → tell them to index first.
