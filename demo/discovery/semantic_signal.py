"""S3 semantic-signal orchestrator: runs the embedding worker in a torch env.

Main process (no torch) writes the file list to a temp json, spawns the worker
subprocess (conda experiments env by default), reads the unit-pair cosine output,
caches it under demo/discovery/cache/<dataset>/<model>_u_<cluster_id>.json, and
re-exposes unit-level evidence for fusion.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any

from .semantic_signal_worker import DEFAULT_MODEL

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_ROOT = os.path.join(REPO_ROOT, "demo", "discovery", "cache")


def _interpreter() -> list[str]:
    direct = os.environ.get("DEMO_EMBED_PYTHON")
    if direct:
        return [direct]
    return ["conda", "run", "-n", "experiments", "--no-capture-output", "python"]


def cache_path(dataset_key: str, cluster_id: str, model: str) -> str:
    safe = model.replace("/", "__")
    return os.path.join(CACHE_ROOT, dataset_key, f"{safe}_u_{cluster_id}.json")


def cluster_evidence(
    file_sources: list[dict[str, str]],
    dataset_key: str,
    cluster_id: str,
    model: str = DEFAULT_MODEL,
    timeout_sec: int = 1800,
    force: bool = False,
) -> dict[str, Any]:
    """Embed files (cached per dataset/cluster) and return signal-shaped evidence.

    On any worker failure (missing torch env, download blocked) returns an empty
    semantic evidence dict so S1/S2 fusion can proceed.
    """
    cache = cache_path(dataset_key, cluster_id, model)
    if not force and os.path.exists(cache):
        with open(cache) as fh:
            data = json.load(fh)
        return _evidence(data)
    if not file_sources:
        return _evidence({"pairs": [], "units": [], "model": model})
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "sources.json")
        outp = os.path.join(tmp, "out.json")
        with open(inp, "w") as fh:
            json.dump({"files": file_sources}, fh)
        env = dict(os.environ, HF_ENDPOINT=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"))
        cmd = [*_interpreter(), "-m", "demo.discovery.semantic_signal_worker", "--input", inp, "--output", outp, "--model", model]
        try:
            proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, timeout=timeout_sec, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            print(f"[semantic] worker spawn failed: {exc}", file=sys.stderr)
            return _evidence({"pairs": [], "units": [], "model": model, "error": str(exc)})
        if proc.returncode != 0:
            print(f"[semantic] worker failed rc={proc.returncode}: {proc.stderr[-2000:]}", file=sys.stderr)
            return _evidence({"pairs": [], "units": [], "model": model, "error": proc.stderr[-500:]})
        with open(outp) as fh:
            data = json.load(fh)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w") as fh:
        json.dump(data, fh)
    return _evidence(data)


def _evidence(data: dict[str, Any]) -> dict[str, Any]:
    pairs = []
    for p in data.get("pairs", []):
        if not all(key in p for key in ("file_a", "unit_a", "file_b", "unit_b", "cosine")):
            continue
        pairs.append({
            "file_a": p["file_a"],
            "unit_a": p["unit_a"],
            "file_b": p["file_b"],
            "unit_b": p["unit_b"],
            "cosine": p["cosine"],
        })
    pairs.sort(key=lambda p: -p["cosine"])
    return {
        "signal": "semantic",
        "model": data.get("model", ""),
        "units": data.get("units", []),
        "pairs": pairs,
    }
