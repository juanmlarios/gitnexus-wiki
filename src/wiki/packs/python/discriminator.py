"""Discriminate Python class idioms from the first source line.

Examples
--------
>>> label_for_class_line("class TokenRecord(TypedDict):")
'TypedDict'
>>> label_for_class_line("class TriggerPayload(BaseModel):")
'pydantic BaseModel'
>>> label_for_class_line("class ArtifactStore(Protocol):")
'Protocol'
>>> label_for_class_line("class Foo:") is None
True
"""

from __future__ import annotations

import re

_BASES_RE = re.compile(r"^\s*class\s+\w+\(([^)]*)\)\s*:")

_LABELS = {
    "TypedDict": "TypedDict",
    "BaseModel": "pydantic BaseModel",
    "Protocol": "Protocol",
    "Enum": "Enum",
    "IntEnum": "IntEnum",
    "StrEnum": "StrEnum",
    "ABC": "ABC",
    "ABCMeta": "ABC",
    "NamedTuple": "NamedTuple",
    "Exception": "Exception",
    "BaseException": "Exception",
}


def label_for_class_line(line: str) -> str | None:
    if not line:
        return None
    m = _BASES_RE.match(line)
    if not m:
        # `class Foo:` with no bases
        return None
    bases = [b.strip() for b in m.group(1).split(",") if b.strip()]
    for b in bases:
        # Strip generic parameters like Generic[T]
        b_root = b.split("[", 1)[0].strip()
        if b_root in _LABELS:
            return _LABELS[b_root]
    # Unknown user-defined base — surface it verbatim so the template can show
    # "extends Foo" rather than guessing a stdlib idiom.
    return f"extends {bases[0]}"
