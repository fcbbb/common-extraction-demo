"""Small pytest adapter for non-Scrapy complex-style datasets."""

from __future__ import annotations

import json
import os


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

try:
    import django

    django.setup()
except ModuleNotFoundError:
    # Collection will report the missing dependency in the normal pytest way.
    pass


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
        if candidates & selected_cases:
            retained.append(item)
    items[:] = retained
