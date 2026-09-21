"""Prepare a time-split Apache Libcloud DNS-provider benchmark.

The extraction snapshot predates the real DNSPod implementation.  Signal only
sees existing DNS drivers at ``SNAPSHOT_COMMIT``.  The later DNSPod commit and
its tests are copied into a separate future-task area for downstream A/B work.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = ROOT / ".cache" / "history" / "apache-libcloud"
DEFAULT_OUT = ROOT / "demo" / "datasets" / "libcloud_dns_history"

REPO_URL = "https://github.com/apache/libcloud"
SNAPSHOT_COMMIT = "1ae9f1c6ad63a14f65476ea3a7265367583ff10d"
FUTURE_COMMIT = "707a725542445b93919eb567a2a3b64bd6222b28"

MEMBERS = [
    "buddyns.py",
    "cloudflare.py",
    "digitalocean.py",
    "luadns.py",
    "nfsn.py",
    "nsone.py",
    "powerdns.py",
    "vultr.py",
]

FUTURE_FILES = [
    "libcloud/common/dnspod.py",
    "libcloud/dns/drivers/dnspod.py",
    "libcloud/test/dns/test_dnspod.py",
]


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True
    ).stdout


def read_blob(repo: Path, commit: str, path: str) -> bytes:
    return git(repo, "show", f"{commit}:{path}")


def write_blob(repo: Path, commit: str, source: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(read_blob(repo, commit, source))


def fixture_paths(repo: Path) -> list[str]:
    output = git(
        repo,
        "ls-tree",
        "-r",
        "--name-only",
        FUTURE_COMMIT,
        "libcloud/test/dns/fixtures/dnspod",
    ).decode("utf-8")
    return [line for line in output.splitlines() if line]


def prepare(repo: Path, out_dir: Path) -> dict[str, Any]:
    repo = repo.resolve()
    out_dir = out_dir.resolve()
    if not (repo / ".git").is_dir():
        raise FileNotFoundError(f"Not a Git checkout: {repo}")
    if out_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing dataset: {out_dir}")

    actual_parent = git(repo, "rev-parse", f"{FUTURE_COMMIT}^").decode().strip()
    if actual_parent != SNAPSHOT_COMMIT:
        raise RuntimeError(
            f"Expected future parent {SNAPSHOT_COMMIT}, found {actual_parent}"
        )

    original_dir = out_dir / "clusters" / "0" / "original"
    entries: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    total_lines = 0
    for index, name in enumerate(MEMBERS):
        upstream_path = f"libcloud/dns/drivers/{name}"
        source = read_blob(repo, SNAPSHOT_COMMIT, upstream_path)
        file_id = f"file_{index:03d}.py"
        target = original_dir / file_id
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        lines = len(source.decode("utf-8").splitlines())
        total_lines += lines
        mapping[name] = file_id
        entries.append(
            {
                "file_id": file_id,
                "name": name,
                "rel_path": upstream_path,
                "subsystem": "dns_driver",
                "lines": lines,
            }
        )

    future_root = out_dir / "future_task"
    for path in FUTURE_FILES:
        write_blob(repo, FUTURE_COMMIT, path, future_root / "ground_truth" / path)
    fixtures = fixture_paths(repo)
    for path in fixtures:
        write_blob(repo, FUTURE_COMMIT, path, future_root / "tests" / path)
    write_blob(
        repo,
        FUTURE_COMMIT,
        "libcloud/test/dns/test_dnspod.py",
        future_root / "tests" / "libcloud/test/dns/test_dnspod.py",
    )

    task_text = f"""# Future task: add DNSPod DNS support

Starting from Apache Libcloud commit `{SNAPSHOT_COMMIT}`, implement DNSPod DNS
support so that the supplied upstream DNSPod test module passes.  The required
public driver behavior includes zone and record listing, lookup, creation and
deletion, provider-specific error translation, and response-to-model mapping.

The extraction phase may read only `clusters/0/original/`.  It must not read
`future_task/ground_truth/` or `future_task/tests/`.  The latter are released
only when running the downstream development evaluation.

This task was realized by upstream commit `{FUTURE_COMMIT}`.  Ground-truth code
is retained solely for auditing and must not be supplied to the coding agent.
"""
    future_root.mkdir(parents=True, exist_ok=True)
    (future_root / "TASK.md").write_text(task_text, encoding="utf-8")

    cluster = {
        "cluster_id": "0",
        "name": "apache_libcloud_dns_drivers_before_dnspod",
        "record_count": len(entries),
        "source_lines": total_lines,
        "selection_rationale": (
            "Existing real REST DNS providers expose repeated zone/record CRUD, "
            "exception translation, and response conversion before DNSPod arrives."
        ),
        "files": entries,
    }
    manifest = {
        "schema": "real-history-libcloud-dns-v1",
        "benchmark_status": "discovery_only",
        "repo": "apache/libcloud",
        "repo_url": REPO_URL,
        "license": "Apache-2.0",
        "source_commit": SNAPSHOT_COMMIT,
        "future_commit": FUTURE_COMMIT,
        "future_task": "future_task/TASK.md",
        "future_test_module": "libcloud/test/dns/test_dnspod.py",
        "development_workspace": {
            "prepared": False,
            "required_scope": "complete_source_checkout",
            "direct": "original complete source_commit checkout",
            "signal": "complete source_commit checkout after tested Signal refactoring",
        },
        "temporal_leakage_policy": {
            "extraction_visible": ["clusters/0/original"],
            "extraction_forbidden": ["future_task/ground_truth", "future_task/tests"],
        },
        "file_id_mapping": mapping,
        "clusters": [cluster],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cluster_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "DATA_INVENTORY.md").write_text(
        "# Apache Libcloud DNS 历史演化数据集\n\n"
        f"- 抽取前快照：`{SNAPSHOT_COMMIT}`\n"
        f"- 未来 DNSPod 提交：`{FUTURE_COMMIT}`\n"
        f"- 抽取可见 driver：{len(entries)} 个，共 {total_lines} 行\n"
        f"- 未来测试 fixture：{len(fixtures)} 个\n"
        "- 抽取输入与未来实现、测试物理隔离。\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    manifest = prepare(args.repo, args.out_dir)
    cluster = manifest["clusters"][0]
    print(
        json.dumps(
            {
                "source_commit": manifest["source_commit"],
                "future_commit": manifest["future_commit"],
                "files": cluster["record_count"],
                "lines": cluster["source_lines"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
