"""Fuse per-signal pair evidence into candidate clusters (files), mutually exclusive.

Each signal (S1 clone / S2 skeleton / S3 semantic) reports per-file-pair evidence.
A pair edge survives when any signal passes its configured bar; surviving edges form
a graph over files, connected components of >= 2 files become candidate clusters.
Components are kept small: per-file edges are capped to the strongest 2 neighbors
before union-find, and oversized components are split by dropping the weakest edge.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

# Frozen defaults (calibrated on codecontest clusters 0/1 vs baseline_b discovery:
# clone/skeleton bars set just above the I/O-idiom noise pairs, semantic bar just
# under the strongest concept-family pairs; both flat in the swept neighborhood).
DEFAULT_CFG = {
    "clone": {"min_shared": 40, "min_containment": 0.35, "min_runs": 1},
    "skeleton": {"min_shared": 18, "min_containment": 0.55, "min_runs": 1},
    "semantic": {"min_cosine": 0.88, "or_top_frac": 0.06},
    "top_neighbors": 2,
    "max_cluster": 6,
}


def _passes(signal: str, cfg: dict[str, Any], edge: dict[str, Any]) -> bool:
    c = cfg[signal]
    if signal == "semantic":
        return edge.get("cosine", 0.0) >= c["min_cosine"]
    tokens = edge.get("shared_tokens", 0)
    containment = edge.get("containment", 0.0)
    runs = edge.get("n_runs", 0)
    return (
        tokens >= c["min_shared"]
        and containment >= c["min_containment"]
        and runs >= c["min_runs"]
    )


def _union(parent: dict[str, str], a: str, b: str) -> None:
    ra, rb = a, b
    while parent[ra] != ra:
        ra = parent[ra]
    while parent[rb] != rb:
        rb = parent[rb]
    if ra != rb:
        parent[rb] = ra


def _root(parent: dict[str, str], a: str) -> str:
    while parent[a] != a:
        a = parent[a]
    return a


def fuse(
    signal_evidences: list[dict[str, Any]],
    file_ids: list[str],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """signal_evidences: output dicts from clone/ast/semantic cluster_evidence()."""
    cfg = cfg or copy.deepcopy(DEFAULT_CFG)
    per_pair: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for ev in signal_evidences:
        signal = ev["signal"]
        for p in ev["pairs"]:
            key = tuple(sorted([p["file_a"], p["file_b"]]))
            if signal == "semantic":
                per_pair[key][signal] = {
                    "cosine": p.get("cosine", 0.0),
                    "shared_tokens": None,
                }
            else:
                per_pair[key][signal] = {
                    "shared_tokens": p["shared_tokens"],
                    "containment": p["containment"],
                    "n_runs": p["n_runs"],
                }
    # Top-frac semantic edges (cosine may be uniform-low when files genuinely differ).
    if "semantic" in {s["signal"] for s in signal_evidences}:
        cosines = sorted(
            (per_pair[k]["semantic"]["cosine"] for k in per_pair if "semantic" in per_pair[k]),
            reverse=True,
        )
        cutoff = (
            cosines[max(0, min(len(cosines) - 1, int(len(cosines) * cfg["semantic"]["or_top_frac"])))]
            if cosines
            else 0.0
        )
    else:
        cutoff = 0.0

    def passes_semantic(cos: float) -> bool:
        if not cosines:
            return False
        return cos >= cfg["semantic"]["min_cosine"] or (cfg["semantic"]["or_top_frac"] > 0 and cos >= cutoff and cos > 0.0)

    edges: list[dict[str, Any]] = []
    for (file_a, file_b), sigs in per_pair.items():
        reasons: dict[str, bool] = {}
        strength: tuple[float, int] = (0.0, 0)
        for signal, s in sigs.items():
            if signal == "semantic":
                ok = passes_semantic(s["cosine"])
                if ok:
                    strength = max(strength, (s["cosine"], 1))
            else:
                ok = _passes(signal, cfg, s)
                if ok:
                    strength = max(strength, (s["containment"], s["shared_tokens"]))
            reasons[signal] = ok
        if not any(reasons.values()):
            continue
        edges.append({
            "file_a": file_a,
            "file_b": file_b,
            "reasons": reasons,
            "strength": strength[0],
            "tokens": max(
                (s["shared_tokens"] for s in sigs.values() if s.get("shared_tokens") is not None),
                default=0,
            ),
            "signals": sigs,
        })
    edges.sort(key=lambda e: (e["strength"], e["tokens"]), reverse=True)

    # Cap per-file degree to the strongest neighbors so unrelated chains do not merge.
    chosen: set[tuple[str, str]] = set()
    degree: dict[str, int] = defaultdict(int)
    for e in edges:
        if degree[e["file_a"]] < cfg["top_neighbors"] and degree[e["file_b"]] < cfg["top_neighbors"]:
            chosen.add(tuple(sorted([e["file_a"], e["file_b"]])))
            degree[e["file_a"]] += 1
            degree[e["file_b"]] += 1

    parent = {f: f for f in file_ids}
    for a, b in chosen:
        _union(parent, a, b)
    comps: dict[str, list[str]] = defaultdict(list)
    for f in file_ids:
        comps[_root(parent, f)].append(f)
    groups = [sorted(v) for v in comps.values() if len(v) >= 2]
    groups.sort(key=lambda g: -sum(e["strength"] for e in edges if {e["file_a"], e["file_b"]} <= set(g)))

    # Split oversized components by dropping the weakest edge until all <= max_cluster.
    while any(len(g) > cfg["max_cluster"] for g in groups):
        over = next(g for g in groups if len(g) > cfg["max_cluster"])
        weak = min(
            (e for e in edges if {e["file_a"], e["file_b"]} <= set(over)),
            key=lambda e: (e["strength"], e["tokens"]),
        )
        parent[weak["file_b"]] = weak["file_b"]
        comps = defaultdict(list)
        for f in file_ids:
            comps[_root(parent, f)].append(f)
        groups = [sorted(v) for v in comps.values() if len(v) >= 2]
        groups.sort(key=lambda g: -sum(e["strength"] for e in edges if {e["file_a"], e["file_b"]} <= set(g)))

    candidates = []
    for members in groups:
        member_set = set(members)
        ev_edges = [e for e in edges if {e["file_a"], e["file_b"]} <= member_set]
        reasons_union: set[str] = set()
        for e in ev_edges:
            reasons_union |= {s for s, ok in e["reasons"].items() if ok}
        shared: dict[str, list[dict[str, Any]]] = {}
        for e in ev_edges:
            for sig, v in e["signals"].items():
                shared.setdefault(sig, []).append({
                    "file_a": e["file_a"],
                    "file_b": e["file_b"],
                    **{k: vv for k, vv in v.items() if k != "signals"},
                })
        candidates.append({
            "members": members,
            "n_members": len(members),
            "signals_hit": sorted(reasons_union),
            "evidence": shared,
            "gain": sum(e["tokens"] for e in ev_edges),
        })
    assigned = {f for g in groups for f in g}
    noise = [f for f in file_ids if f not in assigned]
    return {
        "candidates": candidates,
        "noise": noise,
        "edges_total": len(per_pair),
        "edges_kept": len(edges),
        "config": cfg,
    }
