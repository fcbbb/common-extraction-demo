from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Iterable


def parse_python(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


def common_apis(common_path: Path) -> set[str]:
    module = parse_python(common_path)
    if module is None:
        return set()
    return {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def calls_in_tree(tree: ast.AST) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "common":
                calls.add(func.attr)
    return calls


def common_references_in_tree(tree: ast.AST, apis: set[str]) -> set[str]:
    """Return common.py APIs referenced by imports, calls, or inheritance.

    A class imported from ``common`` and used as a base class is a real API
    dependency even though it never appears as an ``ast.Call``.  Tracking the
    import aliases also handles ``import common as shared`` consistently.
    """
    module_aliases: set[str] = set()
    imported_names: dict[str, str] = {}
    wildcard_import = False
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "common":
                    module_aliases.add(item.asname or "common")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "common":
            for item in node.names:
                if item.name == "*":
                    wildcard_import = True
                elif item.name in apis:
                    imported_names[item.asname or item.name] = item.name
                    used.add(item.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            api = imported_names.get(node.id)
            if api is not None:
                used.add(api)
            elif wildcard_import and node.id in apis:
                used.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in module_aliases and node.attr in apis:
                used.add(node.attr)
    if wildcard_import:
        used.update(apis)
    return used


def used_common_apis(refactored_files: Iterable[Path], apis: set[str]) -> dict[str, list[str]]:
    by_file: dict[str, list[str]] = {}
    for path in sorted(refactored_files):
        module = parse_python(path)
        if module is None:
            by_file[path.name] = []
            continue
        by_file[path.name] = sorted(common_references_in_tree(module, apis))
    return by_file


def measure(result_dir: Path) -> dict[str, object]:
    apis = common_apis(result_dir / "common.py")
    by_file = used_common_apis((result_dir / "refactored").glob("file_*.py"), apis)
    used = set().union(*(set(v) for v in by_file.values())) if by_file else set()
    files_with_api = sum(1 for calls in by_file.values() if calls)
    return {
        "common_api_count": len(apis),
        "common_apis": sorted(apis),
        "used_api_count": len(used),
        "used_apis": sorted(used),
        "api_usage_coverage": len(used) / len(apis) if apis else None,
        "file_count": len(by_file),
        "files_using_common_api": files_with_api,
        "file_api_coverage": files_with_api / len(by_file) if by_file else None,
        "used_by_file": by_file,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure common.py API usage coverage.")
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    payload = json.dumps(measure(args.result_dir), indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
