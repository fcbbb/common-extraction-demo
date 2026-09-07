"""Code-unit extraction (top-level functions / classes / module) from Python sources."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

CONTAINER_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass
class Unit:
    unit_id: str
    kind: str  # "module" | "function" | "class"
    start: int  # 1-based inclusive
    end: int  # 1-based inclusive


def parse_units(source: str) -> tuple[Any | None, list[Unit], str | None]:
    """Return (tree, top-level units, error).

    The module unit spans rows not covered by any top-level function/class.
    A container for a given row is the unit with the smallest span containing it.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return None, [], f"SyntaxError: {exc.msg} at line {exc.lineno}"
    units: list[Unit] = []
    body_span: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, CONTAINER_TYPES):
            units.append(Unit(unit_id=f"{node.__class__.__name__.lower()}:{node.name}", kind="function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "class", start=node.lineno, end=node.end_lineno or node.lineno))
            body_span.append((node.lineno, node.end_lineno or node.lineno))
    # module span: complement rows are handled by container_of_row; module unit only
    # needs to exist for lookup fallback, its span is the whole file.
    last_line = source.count("\n") + 1
    units.insert(0, Unit(unit_id="module", kind="module", start=1, end=last_line))
    return tree, units, None


def container_of_row(units: list[Unit], row: int) -> Unit:
    """Deepest (smallest-span) unit containing the row; module is the fallback."""
    best: Unit = units[0]
    for unit in units:
        if unit.start <= row <= unit.end:
            if unit != best and (unit.end - unit.start) < (best.end - best.start):
                best = unit
    return best


def top_level_containers(tree: Any) -> list[ast.AST]:
    """Body statements of the module that are themselves container definitions."""
    return [node for node in tree.body if isinstance(node, CONTAINER_TYPES)]


def module_body(tree: Any) -> list[ast.AST]:
    """Top-level statements that belong to the module unit (not nested in containers)."""
    return [node for node in tree.body if not isinstance(node, CONTAINER_TYPES)]
