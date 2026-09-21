"""Small pytest adapter shared by package-backed real-code datasets."""

from __future__ import annotations

import json
import os


def pytest_addoption(parser, pluginmanager):
    if not pluginmanager.hasplugin("twisted"):
        parser.addoption("--reactor", default="none", choices=["asyncio", "default", "none"])


def pytest_collection_modifyitems(items):
    selected_cases_raw = os.environ.get("COMMON_EXTRACTION_SELECTED_CASES")
    if selected_cases_raw is None:
        return
    selected_cases = set(json.loads(selected_cases_raw))
    retained = []
    for item in items:
        module_name = item.module.__name__
        class_name = getattr(item, "cls", None)
        module_stem = item.path.stem
        class_stem = class_name.__name__ if class_name is not None else None
        classnames = {module_name, module_name.rsplit(".", 1)[-1], module_stem}
        if class_stem is not None:
            classnames = {f"{classname}.{class_stem}" for classname in classnames}
        candidates = {f"{classname}::{item.name}" for classname in classnames}
        # Pytest can import the same test module as either ``test_foo`` or
        # ``tests.test_foo`` depending on package layout, while JUnit records
        # the latter.  Treat a package-qualified JUnit classname as the same
        # case when its module/class suffix matches the collected item.
        suffix_match = any(
            selected.endswith(f".{candidate}")
            for selected in selected_cases
            for candidate in candidates
        )
        if candidates & selected_cases or suffix_match:
            retained.append(item)
    items[:] = retained
