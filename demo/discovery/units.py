"""Code-unit extraction from Python sources.

Top-level functions/classes remain units, and class methods are also exposed as
``method:Class.method`` units.  Method-level units matter for normal adapter
code: two backend classes can share a small compatibility method without being
near-duplicate classes.
"""
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
            kind = "function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "class"
            units.append(Unit(unit_id=f"{kind}:{node.name}", kind=kind, start=node.lineno, end=node.end_lineno or node.lineno))
            body_span.append((node.lineno, node.end_lineno or node.lineno))
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        units.append(
                            Unit(
                                unit_id=f"method:{node.name}.{child.name}",
                                kind="method",
                                start=child.lineno,
                                end=child.end_lineno or child.lineno,
                            )
                        )
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


def method_containers(tree: Any) -> list[tuple[str, ast.AST]]:
    """Return direct class methods with stable qualified unit names."""
    methods: list[tuple[str, ast.AST]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append((f"method:{node.name}.{child.name}", child))
    return methods


def module_body(tree: Any) -> list[ast.AST]:
    """Top-level statements that belong to the module unit (not nested in containers)."""
    return [node for node in tree.body if not isinstance(node, CONTAINER_TYPES)]
