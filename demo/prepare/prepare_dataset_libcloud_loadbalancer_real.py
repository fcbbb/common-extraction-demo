"""Prepare a real Apache Libcloud load-balancer driver benchmark.

The slice is the complete production ``libcloud.loadbalancer.drivers`` package
from the pinned v3.9.1 release.  Prompt-visible files are anonymized, while the
full upstream package and load-balancer tests are retained for pytest overlays.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "demo" / "datasets" / "libcloud_loadbalancer_real"
DEFAULT_CACHE = ROOT / ".cache" / "apache-libcloud-v3.9.1"
REPO_URL = "https://github.com/apache/libcloud.git"
VERSION = "3.9.1"
TAG = "v3.9.1"
COMMIT = "6c867a3ca299f1b16057fab96bff65564c0ac5fe"

MEMBERS = [
    "alb.py",
    "brightbox.py",
    "cloudstack.py",
    "dimensiondata.py",
    "elb.py",
    "gce.py",
    "ninefold.py",
    "nttcis.py",
    "rackspace.py",
    "slb.py",
]

TESTS_BY_MEMBER = {
    "alb.py": ["test_alb.py"],
    "brightbox.py": ["test_brightbox.py"],
    "cloudstack.py": ["test_cloudstack.py"],
    "dimensiondata.py": ["test_dimensiondata_v2_3.py", "test_dimensiondata_v2_4.py"],
    "elb.py": ["test_elb.py"],
    "gce.py": ["test_gce.py"],
    "ninefold.py": ["test_ninefold.py"],
    "nttcis.py": ["test_nttcis.py"],
    "rackspace.py": ["test_rackspace.py"],
    "slb.py": ["test_slb.py"],
}


def resolve_upstream(upstream: Path | None, cache_dir: Path) -> Path:
    """Return a pinned Libcloud checkout, cloning it on first use."""
    if upstream is not None:
        return upstream.resolve()
    cache_dir = cache_dir.resolve()
    if not (cache_dir / ".git").is_dir():
        if cache_dir.exists():
            raise FileExistsError(
                f"Cache path exists but is not a Git checkout: {cache_dir}. "
                "Remove it or pass --cache-dir elsewhere."
            )
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", TAG, REPO_URL, str(cache_dir)],
            check=True,
        )
    actual_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cache_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual_commit != COMMIT:
        raise RuntimeError(f"Expected Libcloud commit {COMMIT}, found {actual_commit} in {cache_dir}")
    return cache_dir


def test_function_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def prepare(upstream: Path, out_dir: Path) -> dict[str, Any]:
    upstream = upstream.resolve()
    out_dir = out_dir.resolve()
    package = upstream / "libcloud"
    drivers = package / "loadbalancer" / "drivers"
    upstream_tests = package / "test" / "loadbalancer"
    if not drivers.is_dir() or not upstream_tests.is_dir():
        raise FileNotFoundError(f"Not an Apache Libcloud source tree: {upstream}")
    init_text = (package / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{VERSION}"' not in init_text:
        raise RuntimeError(f"Expected Apache Libcloud {VERSION}: {package / '__init__.py'}")
    if out_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing dataset: {out_dir}")

    source_package = out_dir / "source" / "libcloud"
    shutil.copytree(
        package,
        source_package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # Upstream tox creates this local test configuration before pytest starts.
    shutil.copy2(
        source_package / "test" / "secrets.py-dist",
        source_package / "test" / "secrets.py",
    )
    if (upstream / "LICENSE").exists():
        shutil.copy2(upstream / "LICENSE", out_dir / "LICENSE")

    test_dir = out_dir / "tests"
    test_dir.mkdir(parents=True)
    all_tests = sorted({name for names in TESTS_BY_MEMBER.values() for name in names})
    for name in all_tests:
        shutil.copy2(upstream_tests / name, test_dir / name)

    original_dir = out_dir / "clusters" / "0" / "original"
    original_dir.mkdir(parents=True)
    entries: list[dict[str, Any]] = []
    total_lines = 0
    mapping: dict[str, str] = {}
    for index, name in enumerate(MEMBERS):
        source = drivers / name
        if not source.exists():
            raise FileNotFoundError(source)
        file_id = f"file_{index:03d}.py"
        mapping[name] = file_id
        shutil.copy2(source, original_dir / file_id)
        lines = len(source.read_text(encoding="utf-8").splitlines())
        total_lines += lines
        entries.append(
            {
                "file_id": file_id,
                "name": name,
                "rel_path": f"loadbalancer/drivers/{name}",
                "subsystem": "loadbalancer_driver",
                "lines": lines,
                "tests": {"pytest": TESTS_BY_MEMBER[name]},
            }
        )

    test_count = sum(test_function_count(test_dir / name) for name in all_tests)
    cluster = {
        "cluster_id": "0",
        "name": "apache_libcloud_loadbalancer_drivers",
        "record_count": len(entries),
        "source_lines": total_lines,
        "package_name": "libcloud",
        "package_source_rel": "source/libcloud",
        "pytest_conftest": "generic",
        "pytest_extra_paths": [
            {
                "source": "source/libcloud/test/loadbalancer/fixtures",
                "dest": "loadbalancer/fixtures",
            }
        ],
        "test_dir": "demo/datasets/libcloud_loadbalancer_real/tests",
        "shared_test_files": [],
        "selection_rationale": (
            "Complete real production driver directory. DimensionData and NTT CIS "
            "contain a sparse provider-evolution clone family among heterogeneous drivers."
        ),
        "files": entries,
        "test_counts": {
            "pytest_modules": len(all_tests),
            "pytest_test_functions": test_count,
        },
    }
    manifest = {
        "schema": "real-project-libcloud-loadbalancer-v1",
        "repo": "apache/libcloud",
        "repo_url": "https://github.com/apache/libcloud",
        "source_tag": TAG,
        "source_commit": COMMIT,
        "license": "Apache-2.0",
        "slice": "complete_loadbalancer_driver_directory",
        "prompt_input_fields": ["file_id", "source_code"],
        "prompt_excluded_fields": [
            "name",
            "tests",
            "source_commit",
            "selection_rationale",
        ],
        "test_mode": "pytest",
        "file_id_mapping": mapping,
        "clusters": [cluster],
    }
    (out_dir / "cluster_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "DATA_INVENTORY.md").write_text(
        "# Apache Libcloud load-balancer driver slice\n\n"
        f"- Upstream tag: `{TAG}`\n"
        f"- Upstream commit: `{COMMIT}`\n"
        f"- Production driver modules: {len(entries)}\n"
        f"- Production source lines: {total_lines}\n"
        f"- Upstream pytest modules: {len(all_tests)}\n"
        f"- Statically counted test functions: {test_count}\n"
        "- Prompt-visible files are byte-for-byte upstream source with anonymized file names.\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare the pinned Apache Libcloud load-balancer benchmark."
    )
    parser.add_argument(
        "--upstream",
        type=Path,
        help="Existing Apache Libcloud v3.9.1 checkout. Omit to clone the pinned tag.",
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    upstream = resolve_upstream(args.upstream, args.cache_dir)
    manifest = prepare(upstream, args.out_dir)
    cluster = manifest["clusters"][0]
    print(
        json.dumps(
            {
                "repo": manifest["repo"],
                "tag": manifest["source_tag"],
                "commit": manifest["source_commit"],
                "files": cluster["record_count"],
                "lines": cluster["source_lines"],
                "test_functions": cluster["test_counts"]["pytest_test_functions"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
