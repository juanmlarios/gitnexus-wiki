# gitnexus-wiki

A grounded wiki generator for repositories indexed by [gitnexus](https://github.com/abhigyanpatwari/gitnexus). Every file path, symbol name, and statistic emitted by this tool is verified against the gitnexus knowledge graph before a page is written. Hallucinated citations are rejected, not papered over.

## Why this exists

`npx gitnexus wiki` is great for first drafts, but its LLM stage can riff past the graph — it invents file paths from naming patterns, fills in plausible-sounding sibling configs (`production.yaml` next to `local.yaml`), and collapses TypedDict / pydantic / dataclass into a generic "dataclass". This tool keeps the LLM out of any fact-emission path.

## How it works

Three phases per page:

1. **Plan** — query the graph to enumerate clusters, processes, and pages to generate. No LLM.
2. **Build** — pull a fact pack of files / symbols / processes via Cypher; render markdown deterministically from a Jinja template. Optionally fill bounded prose blocks where the LLM is given only the fact pack and must mark unknowns as `[GAP: ...]`.
3. **Verify** — every backticked path and symbol round-trips through gitnexus. Stats round-trip through `gitnexus list`. Failed pages land in `docs/wiki/.failed/` and the CLI exits non-zero.

## Status

`v0.1` — alpha. One end-to-end working page (storage cluster) on Python projects. Architecture supports language packs (`packs/python/`, future: `packs/csharp/`, `packs/typescript/`, etc.).

The **core engine and verifier are language-agnostic**: they work on any gitnexus-indexed repo. Language packs add discrimination niceties (e.g. "this is a TypedDict" vs "this is a Protocol"). A repo with no matching pack still gets a verified, accurate (slightly drier) wiki.

## Install

Recommended (global, for use across projects):

```bash
git clone https://github.com/juanmlarios/gitnexus-wiki ~/.claude/skills/gitnexus-wiki
# First invocation in any project sets up an isolated venv automatically.
```

Pip-installable for CI:

```bash
pip install git+https://github.com/juanmlarios/gitnexus-wiki
```

## Use

In any gitnexus-indexed project:

```bash
# Full regen
gitnexus-wiki

# Just verify existing wiki against the current graph (CI gate)
gitnexus-wiki --verify-only

# Single page
gitnexus-wiki --page storage

# Skip optional prose blocks (deterministic-only output)
gitnexus-wiki --no-prose
```

The tool detects the current project's `.gitnexus/` directory and the active repo name automatically.

## Per-project overrides

Optional `.claude/wiki.yaml` in the project root:

```yaml
out_dir: docs/wiki        # default
pages: [storage, ingest]  # filter; omit for all
prose: true               # fill prose blocks (requires LLM access)
extra_clusters: {}        # merge or split communities
```

## Status of language packs

| Language | Pack | Status |
|----------|------|--------|
| Python   | `packs/python/` | Working |
| TypeScript / JavaScript | `packs/typescript/` | Planned |
| C# | `packs/csharp/` | Planned |
| Rust | `packs/rust/` | Planned |
| Go | `packs/go/` | Planned |
| Java | `packs/java/` | Planned |

Without a pack, the core engine still produces a verified wiki for any language gitnexus indexes — the prose just stays generic.

## Contributing

Issues and PRs welcome at [the GitHub repo](https://github.com/juanmlarios/gitnexus-wiki). Language packs are especially welcome — see `src/wiki/packs/python/` for the pattern.

## License

MIT
