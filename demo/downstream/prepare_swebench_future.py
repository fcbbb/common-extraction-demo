"""Reconstruct SWE-rebench future commits on top of C0 in the history cache.

SWE-rebench publishes the future change as two diffs (gold + tests) rather than
a merge SHA, and squash-merges upstream make the real merge commit unusable.
The downstream runner instead needs a real ``future_end`` commit inside the
repo cache: :func:`demo.downstream.core.materialize` exports future tests and
ground truth from it, and ``_verify_history`` requires it to descend from C0.

This module replays ``tests.patch`` + ``gold.patch`` on C0 and commits the
result on the ``downstream/swebench`` branch. Commit metadata is fixed
(git-ignored identity, pinned date, deterministic message) so re-running this
module on a fresh clone of the same repository reproduces the exact SHA
recorded in ``tasks.json``.

Usage::

    python -m demo.downstream.prepare_swebench_future \
        --task-dir demo/datasets/swe_rebench_screen/tasks/<instance_id> \
        --slug titus-ong/chordparser --c0 <sha>

The task directory is produced by the SWE-rebench screening step and must
contain ``gold.patch``, ``tests.patch`` and ``meta.json`` (with ``repo`` and
``base_commit``). ``--slug``/``--c0`` are conveniences that default to the
meta values. Repositories are cloned through the ghfast.top mirror with a
direct-GitHub fallback because direct clones time out on this network.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO_CACHE = ROOT / ".cache" / "history"
BRANCH = "downstream/swebench"
MIRROR_PREFIX = "https://ghfast.top/"
# Fixed commit metadata: identity, date, and message must never change for an
# existing task, otherwise the recorded future SHA stops being reproducible.
COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "downstream-gate",
    "GIT_AUTHOR_EMAIL": "downstream-gate@localhost",
    "GIT_AUTHOR_DATE": "2026-09-24T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "downstream-gate",
    "GIT_COMMITTER_EMAIL": "downstream-gate@localhost",
    "GIT_COMMITTER_DATE": "2026-09-24T00:00:00+00:00",
}


def _run(argv: list[str], cwd: Path, env: dict[str, str] | None = None,
         check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                            env={**_full_env(), **(env or {})})
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({argv[:3]}): {result.stderr[-2000:]}")
    return result


def _full_env() -> dict[str, str]:
    import os
    return dict(os.environ)


def ensure_clone(slug: str, repo_cache: Path) -> Path:
    repo = repo_cache / slug
    if (repo / ".git").is_dir():
        return repo.resolve()
    repo.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{slug}.git"
    for candidate in (MIRROR_PREFIX + url, url):
        result = subprocess.run(
            ["git", "clone", candidate, str(repo)],
            cwd=str(repo.parent), capture_output=True, text=True, timeout=900)
        if result.returncode == 0:
            return repo.resolve()
    raise RuntimeError(f"git clone failed for {slug}: {result.stderr[-2000:]}")


def build_future(task_dir: Path, slug: str, c0: str, repo_cache: Path) -> str:
    repo = ensure_clone(slug, repo_cache)
    gold = (task_dir / "gold.patch").read_bytes()
    tests = (task_dir / "tests.patch").read_bytes()

    _run(["git", "cat-file", "-e", f"{c0}^{{commit}}"], repo)
    # Detach at C0 so the branch pointer is recreated, then replay both
    # patches. ``git apply`` with --whitespace=nowarn keeps patch-local
    # trailing whitespace from failing replay on stricter git versions.
    _run(["git", "checkout", "-f", "--quiet", "--detach", c0], repo)
    _run(["git", "clean", "-fdxq"], repo)
    for name, data in (("tests.patch", tests), ("gold.patch", gold)):
        target = (task_dir / name).resolve()
        target.write_bytes(data)
        apply = _run(["git", "apply", "--whitespace=nowarn", str(target)], repo,
                     check=False)
        if apply.returncode != 0:
            raise RuntimeError(f"{name} does not apply on {c0}: "
                               f"{apply.stderr[-1500:]}")
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "--quiet", "--no-gpg-sign", "-m",
          f"swebench future: tests + gold ({task_dir.name})"], repo, env=COMMIT_ENV)
    future = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    _run(["git", "branch", "-f", BRANCH, future], repo)
    default = _run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                   repo, check=False).stdout.strip() or "main"
    _run(["git", "checkout", "-f", "--quiet", default], repo)
    _run(["git", "clean", "-fdxq"], repo)
    _run(["git", "checkout", "-f", "--quiet", "."], repo)
    return future


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task-dir", type=Path, required=True,
                        help="Screening task directory with gold.patch/tests.patch/meta.json")
    parser.add_argument("--repo-cache", type=Path, default=DEFAULT_REPO_CACHE)
    parser.add_argument("--slug", default=None, help="owner/repo; default from meta.json")
    parser.add_argument("--c0", default=None, help="base commit; default from meta.json")
    args = parser.parse_args()

    meta = json.loads((args.task_dir / "meta.json").read_text())
    slug = args.slug or meta["repo"]
    c0 = args.c0 or meta["base_commit"]
    future = build_future(args.task_dir, slug, c0, args.repo_cache)
    print(json.dumps({"task": args.task_dir.name, "slug": slug, "c0": c0,
                      "future_end": future, "branch": BRANCH}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
