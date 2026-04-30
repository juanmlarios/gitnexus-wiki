"""Jinja extension: `{% prose "slot-name" %}...fallback...{% endprose %}`.

The block body is the deterministic fallback. When a `prose_handler` is
attached to the environment, that handler decides whether to return the
fallback verbatim or replace it with LLM-generated prose.
"""

from __future__ import annotations

from typing import Callable

from jinja2 import nodes
from jinja2.ext import Extension


class ProseExtension(Extension):
    tags = {"prose"}

    def __init__(self, environment):
        super().__init__(environment)
        environment.extend(prose_handler=None, prose_fact_pack=None)

    def parse(self, parser):
        lineno = next(parser.stream).lineno
        # Single positional arg: the slot name (string literal or expression).
        slot_expr = parser.parse_expression()
        body = parser.parse_statements(["name:endprose"], drop_needle=True)
        return nodes.CallBlock(
            self.call_method("_render_prose", [slot_expr]),
            [],
            [],
            body,
        ).set_lineno(lineno)

    def _render_prose(self, slot_name: str, caller: Callable[[], str]) -> str:
        fallback = caller().strip()
        handler = self.environment.prose_handler
        fact_pack = self.environment.prose_fact_pack
        if handler is None or fact_pack is None:
            return fallback
        return handler(slot_name=slot_name, fallback=fallback, fact_pack=fact_pack)
