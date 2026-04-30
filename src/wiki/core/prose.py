"""Bounded LLM prose generation via the local `claude` CLI.

Each call shells out to:
    claude --print --model <m> --system-prompt <SYSTEM> --tools "" \
           --disable-slash-commands --no-session-persistence <user_prompt>

This re-uses the user's existing Claude Code authentication — no API key
handling here, no anthropic SDK dependency.

The output is plain text. The caller is responsible for verification
(every backticked symbol/path must round-trip through gitnexus).

A SHA256 cache keyed by (prompt_version, slot_name, fact_pack_json,
system_prompt) lives at <project_root>/.gitnexus-wiki-cache/prose/.
Cache hits skip the subprocess entirely, so most regens are free.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# Bump when the SYSTEM_PROMPT or any contract semantics change so old
# cache entries are naturally invalidated.
PROMPT_VERSION = "3"

DEFAULT_MODEL = "sonnet"
TIMEOUT_S = 60
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """\
You write technical prose for an auto-generated wiki page describing a
software repository. Your only input is a fact pack: a JSON document
containing all and only the facts you may use.

You will be asked to fill one or more named "slots" for the same page.
Return your output as a JSON object whose keys are the exact slot names
and whose values are the prose for each slot.

ABSOLUTE RULES — violations break the build:
1. Every file path, symbol name, count, or technical term you mention
   must appear VERBATIM in the fact pack. Never invent. Never modify.
2. When citing a file, ALWAYS use the full path exactly as it appears in
   the fact pack (e.g. `workplans/ingest-agent/apply_brief.py`, NEVER the
   basename alone like `apply_brief.py`).
3. Function and class names are NOT filenames. The fact pack lists
   files under `path` and symbols under `symbols.name` — do not confuse
   them. If you only see `csv_to_tokens` as a symbol name, do not write
   `csv_to_tokens.py` as a filename.
4. Don't guess what something does from its name. If the fact pack says
   `pick_lam` is a function, describe it as "function pick_lam" — do
   NOT speculate that it picks "a language model" or anything else not
   stated in the fact pack.
5. If you would need a fact that isn't in the fact pack, set that slot's
   value to "[GAP: <brief description>]".
6. Do not extrapolate. If the fact pack lists 3 classes, do not write
   "many" or "and others". If only one config file appears, do not
   imply siblings.
7. No editorializing. No "elegantly", "powerful", "well-designed",
   "robust", "comprehensive". Stick to what the fact pack proves.
8. Each slot's output is plain prose — no headings, no bullets, no code
   fences, unless that slot's task asks for them.
9. Stay within each slot's word budget.
10. Return ONLY a JSON object. No prose outside the JSON. No markdown
    code fences around the JSON.
"""


class ProseError(RuntimeError):
    pass


class ClaudeNotFoundError(ProseError):
    pass


@dataclass
class ProseSlot:
    name: str  # e.g. "cluster-intro", "architecture-intro"
    task: str  # what the LLM should do
    word_budget: int = 80


# Centralized slot definitions. Templates reference these by name. Adding a
# new prose slot means: declare it here, then drop a {% prose "name" %} block
# into the template with deterministic fallback text in its body.
SLOT_SPECS: dict[str, "ProseSlot"] = {
    "repo-intro": ProseSlot(
        name="repo-intro",
        task=(
            "Write 1–2 sentences introducing this repository. Name what kind "
            "of code dominates (based on top-level dirs and largest cluster). "
            "Do not editorialize about quality."
        ),
        word_budget=60,
    ),
    "architecture-intro": ProseSlot(
        name="architecture-intro",
        task=(
            "Open the architecture page with a paragraph describing the "
            "shape of the codebase: how many top-level entries, where the "
            "bulk of the symbols live, and what the largest cluster owns. "
            "Strictly based on the fact pack — do not infer intent."
        ),
        word_budget=110,
    ),
    "architecture-clusters": ProseSlot(
        name="architecture-clusters",
        task=(
            "Briefly describe the cluster landscape: how many multi-symbol "
            "clusters exist and which one or two are the largest. Mention the "
            "biggest cluster's largest file by name."
        ),
        word_budget=80,
    ),
    "architecture-seams": ProseSlot(
        name="architecture-seams",
        task=(
            "If inter_cluster_edges has any rows, describe the dominant "
            "cross-cluster CALLS relationships in 2–3 sentences. If the list "
            "is empty, say the codebase has no inter-cluster CALLS edges in "
            "the index."
        ),
        word_budget=80,
    ),
    "processes-intro": ProseSlot(
        name="processes-intro",
        task=(
            "Open the execution-flows catalog with one paragraph describing "
            "how many flows are traced, the longest flow's length, and which "
            "cluster contributes the most flows."
        ),
        word_budget=60,
    ),
    "cluster-intro": ProseSlot(
        name="cluster-intro",
        task=(
            "Open this cluster page with one paragraph describing what kind "
            "of code lives here — name the cluster, the file(s) involved, "
            "the headline class or function, and what role it plays based "
            "on its processes (if any)."
        ),
        word_budget=70,
    ),
    "cluster-role": ProseSlot(
        name="cluster-role",
        task=(
            "Describe in 1–2 sentences how this cluster relates to the rest "
            "of the codebase, based ONLY on its processes and any classes/"
            "functions that suggest interfaces (Protocol, ABC, BaseModel)."
        ),
        word_budget=60,
    ),
}


@dataclass
class ProseResult:
    text: str
    cached: bool
    fallback: bool  # true if we returned the deterministic body
    attempts: int


# ---- subprocess wrapper -----------------------------------------------------


def _claude_available() -> bool:
    return shutil.which("claude") is not None


def _call_claude(user_prompt: str, *, model: str = DEFAULT_MODEL) -> str:
    if not _claude_available():
        raise ClaudeNotFoundError(
            "`claude` CLI not found on PATH. Install Claude Code or run without --prose."
        )
    cmd = [
        "claude",
        "--print",
        "--model", model,
        "--system-prompt", SYSTEM_PROMPT,
        "--tools", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format", "text",
        user_prompt,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    if res.returncode != 0:
        raise ProseError(f"`claude --print` failed (exit {res.returncode}): {res.stderr.strip()}")
    return res.stdout.strip()


def _parse_json_response(raw: str) -> dict | None:
    """Best-effort JSON parse: handles bare JSON, ```json fences, and noise
    around a single top-level object."""
    if not raw:
        return None
    s = raw.strip()
    # Strip code fences (```json ... ``` or ``` ... ```).
    if s.startswith("```"):
        first_nl = s.find("\n")
        last_fence = s.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            s = s[first_nl + 1 : last_fence].strip()
    # Try direct parse.
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    # Last resort: find the outermost { ... } block.
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        try:
            v = json.loads(s[start : end + 1])
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ---- cache ------------------------------------------------------------------


def _cache_path(project_root: Path) -> Path:
    return project_root / ".gitnexus-wiki-cache" / "prose"


def _cache_key(slot_name: str, fact_pack_json: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(b"\0")
    h.update(model.encode())
    h.update(b"\0")
    h.update(SYSTEM_PROMPT.encode())
    h.update(b"\0")
    h.update(slot_name.encode())
    h.update(b"\0")
    h.update(fact_pack_json.encode())
    return h.hexdigest()


def _batch_cache_key(slot_names: list[str], fact_pack_json: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(b"\0batch\0")
    h.update(model.encode())
    h.update(b"\0")
    h.update(SYSTEM_PROMPT.encode())
    h.update(b"\0")
    for name in sorted(slot_names):
        h.update(name.encode())
        h.update(b"\0")
    h.update(fact_pack_json.encode())
    return h.hexdigest()


def _read_cache(project_root: Path, key: str) -> str | None:
    p = _cache_path(project_root) / f"{key}.md"
    if p.exists():
        return p.read_text()
    return None


def _write_cache(project_root: Path, key: str, text: str) -> None:
    d = _cache_path(project_root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.md").write_text(text)


# ---- top-level entry --------------------------------------------------------


def generate_prose(
    *,
    slot: ProseSlot,
    fact_pack: dict[str, Any],
    fallback_text: str,
    project_root: Path,
    model: str = DEFAULT_MODEL,
    use_cache: bool = True,
) -> ProseResult:
    """Generate prose for a single slot.

    Always returns *something*. If the LLM is unavailable, fails repeatedly,
    or produces unverifiable output, the deterministic `fallback_text` is
    returned and `result.fallback == True`.
    """
    fact_pack_json = json.dumps(fact_pack, sort_keys=True, default=str)
    key = _cache_key(slot.name, fact_pack_json, model)

    if use_cache:
        cached = _read_cache(project_root, key)
        if cached is not None:
            return ProseResult(text=cached, cached=True, fallback=False, attempts=0)

    user_prompt = (
        f"Slot: {slot.name}\n"
        f"Word budget: {slot.word_budget} words.\n"
        f"Task: {slot.task}\n\n"
        f"Fact pack (JSON — your only allowed source of facts):\n{fact_pack_json}\n"
    )

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            text = _call_claude(user_prompt, model=model)
        except ClaudeNotFoundError as e:
            LOG.warning("%s — using deterministic fallback for slot %r", e, slot.name)
            return ProseResult(text=fallback_text, cached=False, fallback=True, attempts=attempt)
        except (ProseError, subprocess.TimeoutExpired) as e:
            last_err = e
            LOG.warning("prose call failed (attempt %d/%d) for slot %r: %s",
                        attempt, MAX_ATTEMPTS, slot.name, e)
            continue

        if "[GAP:" in text:
            LOG.warning("prose for slot %r emitted GAP marker; will retry or fall back", slot.name)
            last_err = ProseError("GAP marker in output")
            continue

        if use_cache:
            _write_cache(project_root, key, text)
        return ProseResult(text=text, cached=False, fallback=False, attempts=attempt)

    LOG.warning("prose slot %r exhausted retries (%s); using deterministic fallback",
                slot.name, last_err)
    return ProseResult(text=fallback_text, cached=False, fallback=True, attempts=MAX_ATTEMPTS)


# ---- batched (per-page) prose -----------------------------------------------


@dataclass
class BatchedProseResult:
    """Per-slot result for a single per-page batched call."""
    texts: dict[str, str]                 # slot_name -> generated prose
    cached: bool
    fallback_slots: list[str]             # slots that ended up using fallback
    attempts: int


def generate_batched_prose(
    *,
    slot_specs: list[ProseSlot],
    fallbacks: dict[str, str],
    fact_pack: dict[str, Any],
    project_root: Path,
    model: str = DEFAULT_MODEL,
    use_cache: bool = True,
) -> BatchedProseResult:
    """One LLM call per page that fills every prose slot at once.

    Returns a (slot_name -> text) map. Any slot the LLM didn't return a value
    for, or whose value parses as a [GAP: ...] marker, is filled from the
    `fallbacks` map.
    """
    if not slot_specs:
        return BatchedProseResult(texts={}, cached=False, fallback_slots=[], attempts=0)

    fact_pack_json = json.dumps(fact_pack, sort_keys=True, default=str)
    slot_names = [s.name for s in slot_specs]
    key = _batch_cache_key(slot_names, fact_pack_json, model)

    if use_cache:
        cached = _read_cache(project_root, key)
        if cached is not None:
            try:
                texts = json.loads(cached)
                # Pad missing keys (in case spec list grew) and report none as fallback
                # — anything that's missing gets a fallback in the substitution step.
                return BatchedProseResult(
                    texts={k: v for k, v in texts.items() if isinstance(v, str)},
                    cached=True, fallback_slots=[], attempts=0,
                )
            except json.JSONDecodeError:
                LOG.warning("cached prose at %s was not valid JSON; regenerating", key)

    # Build prompt: list each slot, its task, its budget.
    slot_lines = []
    for s in slot_specs:
        slot_lines.append(f"- {s.name} (≤ {s.word_budget} words): {s.task}")
    user_prompt = (
        "Fill the following prose slots for one page. Return ONLY a JSON object "
        "whose keys are exactly the slot names below — no surrounding prose, "
        "no markdown code fences, just the JSON.\n\n"
        "Slots:\n" + "\n".join(slot_lines) + "\n\n"
        f"Fact pack (JSON — your only allowed source of facts):\n{fact_pack_json}\n"
    )

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _call_claude(user_prompt, model=model)
        except ClaudeNotFoundError as e:
            LOG.warning("%s — using deterministic fallback for entire page", e)
            return BatchedProseResult(
                texts={}, cached=False, fallback_slots=slot_names, attempts=attempt,
            )
        except (ProseError, subprocess.TimeoutExpired) as e:
            last_err = e
            LOG.warning("batched prose call failed (attempt %d/%d): %s",
                        attempt, MAX_ATTEMPTS, e)
            continue

        payload = _parse_json_response(raw)
        if payload is None:
            last_err = ProseError("batched response was not a parseable JSON object")
            LOG.warning("batched prose returned non-JSON (attempt %d/%d): %.200r",
                        attempt, MAX_ATTEMPTS, raw)
            continue

        # Detect any GAP markers; treat as missing for that slot only.
        cleaned: dict[str, str] = {}
        fallback_slots: list[str] = []
        for s in slot_specs:
            value = payload.get(s.name)
            if not isinstance(value, str) or "[GAP:" in value:
                fallback_slots.append(s.name)
                continue
            cleaned[s.name] = value.strip()

        if use_cache and cleaned:
            _write_cache(project_root, key, json.dumps(cleaned, sort_keys=True))
        return BatchedProseResult(
            texts=cleaned, cached=False, fallback_slots=fallback_slots, attempts=attempt,
        )

    LOG.warning("batched prose exhausted retries (%s); using deterministic fallback for page",
                last_err)
    return BatchedProseResult(
        texts={}, cached=False, fallback_slots=slot_names, attempts=MAX_ATTEMPTS,
    )
