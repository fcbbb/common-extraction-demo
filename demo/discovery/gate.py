"""LLM judgment gate: per-candidate DeepSeek call validating tool-discovered groups.

Accepts/rejects each candidate cluster, may drop members, and names the shared
interface.  Produces the final discovery.json in the baseline_b schema:
  {clusters: [{cluster_id, members, shared_concept, shared_interface, key_variations}],
   noise: [...], rationale: "..."}
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any

from demo.baselines.json_utils import extract_json_object
from demo.baselines.llm_client import DeepSeekClient
from demo.baselines.runner_utils import (
    PROMPT_DIR,
    build_user_prompt,
    read_text,
    save_call_log,
    status,
    write_json,
)
from .units import unit_is_ancestor

SYSTEM_PATH = PROMPT_DIR / "signal_gate_system.txt"
USER_PATH = PROMPT_DIR / "signal_gate_user_template.txt"

# The LLM gate judges whether a reusable abstraction exists. These thresholds
# are a second, deterministic screen for whether the abstraction is large
# enough to justify materializing common.py and a call site in every member.
DEFAULT_EXTRACTION_SCREEN = {
    "min_shared_tokens": 18,
    "min_estimated_savings": 8,
    "wrapper_tokens_per_file": 8,
}


def _evidence_text(candidate: dict[str, Any]) -> str:
    lines = []
    ev = candidate.get("evidence", {})
    for sig, items in ev.items():
        for it in items:
            if sig == "semantic":
                lines.append(
                    f"semantic: {it['file_a']}({it.get('unit_a')})~{it['file_b']}({it.get('unit_b')}) "
                    f"cosine={it['cosine']}"
                )
            else:
                lines.append(
                    f"{sig}: {it['file_a']}({it.get('unit_a')})~{it['file_b']}({it.get('unit_b')}) "
                    f"shared={it.get('shared_tokens')} tokens "
                    f"containment={it.get('containment')} runs={it.get('n_runs')}"
                )
    return "\n".join(lines) if lines else "(no pair evidence recorded)"


def _group_screen(
    members: list[str],
    units: dict[str, list[str]],
    evidence: dict[str, list[dict[str, Any]]],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate whether a gated group is worth sending to extraction.

    This deliberately uses only concrete clone/skeleton shared-token evidence.
    Semantic similarity can help the LLM find a candidate, but cannot by itself
    establish token savings. The estimate is conservative for multi-file
    groups: use the strongest concrete pair, capped by the average edge share,
    and charge a wrapper budget for every member file.
    """
    screen = dict(DEFAULT_EXTRACTION_SCREEN)
    screen.update(cfg or {})
    member_set = set(members)
    allowed_units = {
        (file_id, unit_id)
        for file_id, unit_ids in units.items()
        for unit_id in unit_ids
    }
    pair_shared: dict[tuple[str, str], int] = {}
    concrete_files: set[str] = set()
    for records in evidence.values():
        for record in records:
            file_a, file_b = record.get("file_a"), record.get("file_b")
            unit_a, unit_b = record.get("unit_a"), record.get("unit_b")
            if file_a not in member_set or file_b not in member_set:
                continue
            if (file_a, unit_a) not in allowed_units or (file_b, unit_b) not in allowed_units:
                continue
            shared = record.get("shared_tokens")
            if not isinstance(shared, (int, float)) or shared <= 0:
                continue
            pair = tuple(sorted((file_a, file_b)))
            pair_shared[pair] = max(pair_shared.get(pair, 0), int(shared))
            concrete_files.update((file_a, file_b))

    member_count = len(member_set)
    max_pair_shared = max(pair_shared.values(), default=0)
    total_pair_shared = sum(pair_shared.values())
    edge_count = max(member_count - 1, 1)
    estimated_component_tokens = min(
        max_pair_shared,
        total_pair_shared / edge_count if total_pair_shared else 0,
    )
    estimated_savings = (
        (member_count - 1) * estimated_component_tokens
        - member_count * screen["wrapper_tokens_per_file"]
    )
    reasons: list[str] = []
    if len(concrete_files) < member_count:
        reasons.append("not_all_members_have_concrete_shared_token_evidence")
    if max_pair_shared < screen["min_shared_tokens"]:
        reasons.append("shared_component_below_minimum_tokens")
    if estimated_savings < screen["min_estimated_savings"]:
        reasons.append("estimated_savings_below_minimum")
    return {
        "accepted": not reasons,
        "members": sorted(member_set),
        "units": {file_id: sorted(unit_ids) for file_id, unit_ids in units.items()},
        "member_count": member_count,
        "concrete_pairs": len(pair_shared),
        "concrete_files": sorted(concrete_files),
        "max_pair_shared_tokens": max_pair_shared,
        "estimated_component_tokens": round(estimated_component_tokens, 2),
        "wrapper_tokens_per_file": screen["wrapper_tokens_per_file"],
        "estimated_savings": round(estimated_savings, 2),
        "reasons": reasons,
    }


def _screen_groups(
    clusters: list[dict[str, Any]],
    cfg: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    before: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for cluster in clusters:
        screening = dict(cluster.get("screening") or {})
        screen_members = sorted(screening.get("members", []))
        screen_units = screening.get("units") or {}
        if screen_members != sorted(cluster.get("members", [])) or screen_units != cluster.get("units", {}):
            screening["accepted"] = False
            screening.setdefault("reasons", []).append("unit_scope_changed_after_normalization")
        summary = {
            "cluster_id": cluster.get("cluster_id"),
            "members": cluster.get("members", []),
            "units": cluster.get("units", {}),
            "screening": screening,
        }
        before.append(summary)
        if screening.get("accepted"):
            kept.append(cluster)
        else:
            dropped.append(summary)
    report = {
        "config": {**DEFAULT_EXTRACTION_SCREEN, **(cfg or {})},
        "before_count": len(clusters),
        "after_count": len(kept),
        "dropped_count": len(dropped),
        "before": before,
        "after": [
            {
                "cluster_id": cluster.get("cluster_id"),
                "members": cluster.get("members", []),
                "units": cluster.get("units", {}),
                "screening": cluster.get("screening", {}),
            }
            for cluster in kept
        ],
        "dropped": dropped,
    }
    return kept, report


def _validate_decision(dec: dict[str, Any], candidate_id: str, members: list[str]) -> None:
    if dec.get("candidate_id") != candidate_id:
        raise ValueError(f"gate decision candidate_id mismatch: {dec.get('candidate_id')!r} != {candidate_id!r}")
    groups = dec.get("groups")
    if not isinstance(groups, list):
        raise ValueError("gate decision missing groups list")
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("each group must be an object")
        sub = group.get("members")
        if not isinstance(sub, list) or len(sub) < 2:
            raise ValueError("each group must keep a members list of >= 2 files")
        if any(m not in members for m in sub) or len(set(sub)) != len(sub):
            raise ValueError("members must be a unique subset of the candidate's members")
        # A file may occur in multiple groups when different units from that
        # file participate in different reusable components.
        seen |= set(sub)


def _normalize_scopes(clusters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve parent/child and repeated unit scopes while retaining file reuse.

    A file may legitimately occur in several groups, but a parent class/module
    scope cannot coexist with a child method scope, and the same exact unit may
    only be assigned once.  This operates on unit ids, not file ids.
    """
    occurrences = []
    for cluster_index, cluster in enumerate(clusters):
        for file_id, unit_ids in (cluster.get("units") or {}).items():
            for unit_id in unit_ids:
                occurrences.append((cluster_index, file_id, unit_id))

    rejected_ancestors = {
        (cluster_index, file_id, unit_id)
        for cluster_index, file_id, unit_id in occurrences
        if any(
            other_file == file_id
            and (other_cluster != cluster_index or other_unit != unit_id)
            and unit_is_ancestor(unit_id, other_unit)
            for other_cluster, other_file, other_unit in occurrences
        )
    }

    normalized: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    claimed: set[tuple[str, str]] = set()
    for cluster_index, cluster in enumerate(clusters):
        units: dict[str, list[str]] = {}
        dropped: list[dict[str, str]] = []
        for file_id, unit_ids in (cluster.get("units") or {}).items():
            for unit_id in unit_ids:
                key = (cluster_index, file_id, unit_id)
                if key in rejected_ancestors:
                    dropped.append({"file_id": file_id, "unit_id": unit_id, "reason": "ancestor_of_other_unit"})
                    continue
                claim = (file_id, unit_id)
                if claim in claimed:
                    dropped.append({"file_id": file_id, "unit_id": unit_id, "reason": "duplicate_unit"})
                    continue
                claimed.add(claim)
                units.setdefault(file_id, []).append(unit_id)
        members = sorted(units)
        if len(members) >= 2:
            item = dict(cluster)
            item["members"] = members
            item["units"] = {file_id: sorted(set(unit_ids)) for file_id, unit_ids in units.items()}
            normalized.append(item)
        else:
            dropped.extend(
                {"file_id": file_id, "unit_id": unit_id, "reason": "cluster_below_two_files"}
                for file_id, unit_ids in units.items()
                for unit_id in unit_ids
            )
        if dropped:
            audit.append({"cluster_id": cluster.get("cluster_id"), "dropped": dropped})
    return normalized, audit


def _call_gate(
    client: DeepSeekClient,
    cluster_id: str,
    candidate_id: str,
    members: list[str],
    units: list[dict[str, Any]],
    sources: dict[str, str],
    evidence_text: str,
    out_dir: Path,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "cluster_id": cluster_id,
        "candidates": [
            {
                "candidate_id": candidate_id,
                "members": members,
                "units": units,
                "files": [{"file_id": fid, "source_code": sources[fid]} for fid in members],
                "evidence": evidence_text,
            }
        ],
    }
    system_prompt = read_text(SYSTEM_PATH)
    user_prompt = build_user_prompt(USER_PATH, payload)
    schema_hint = '{"candidate_id":"c0","groups":[{"members":["a.py","b.py"],"shared_concept":"...","shared_interface":[{"name":"f","signature_guess":"f(...)","role":"..."}],"key_variations":["..."]}]}'
    prompt_paths = {"system": str(SYSTEM_PATH), "user_template": str(USER_PATH)}
    errors: list[str] = []
    for attempt in range(2):
        final = user_prompt
        if attempt == 1:
            final = (
                user_prompt
                + "\n\nYour previous response could not be parsed or did not match the required schema. "
                + f"Return one complete valid JSON object only, matching this schema: {schema_hint}"
                + f"\nParser error: {errors[-1]}"
            )
        response = client.chat(system_prompt, final)
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if attempt == 0 else f".retry{attempt}"
        (out_dir / f"gate_{candidate_id}_raw_response{suffix}.txt").write_text(response.content, encoding="utf-8")
        write_json(out_dir / f"gate_{candidate_id}_raw_api_response{suffix}.json", response.raw)
        save_call_log(
            out_dir / f"gate_{candidate_id}_call_log{suffix}.json",
            response,
            "signal_gate",
            cluster_id,
            prompt_paths,
        )
        try:
            decision = extract_json_object(response.content)
            _validate_decision(decision, candidate_id, members)
            return decision
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
    raise ValueError("gate JSON parse/schema failed after one retry: " + " | ".join(errors))


def run_gate(
    cluster: dict[str, Any],
    sources: list[dict[str, str]],
    candidates_doc: dict[str, Any],
    out_dir: Path,
    skip_gate: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Returns final discovery doc (baseline_b schema) or None when gate skipped."""
    cfg = cfg or {}
    cluster_id = cluster["cluster_id"]
    all_files = [f["file_id"] for f in cluster["files"]]
    if skip_gate or not candidates_doc.get("candidates"):
        return None
    client = DeepSeekClient(timeout_sec=cfg.get("api_timeout_sec"), max_tokens=cfg.get("max_output_tokens"))
    by_id = {s["file_id"]: s["source_code"] for s in sources}
    clusters: list[dict[str, Any]] = []
    noise: list[str] = []
    rationale: list[str] = []
    total_candidates = len(candidates_doc["candidates"])
    gate_workers = max(1, min(int(cfg.get("gate_workers", 1)), total_candidates))

    def call_candidate(index: int, cand: dict[str, Any]) -> tuple[dict[str, Any] | None, Exception | None]:
        candidate_id = f"c{index}"
        members = cand["members"]
        candidate_units = cand.get("units", [])
        status(
            f"signal cluster {cluster_id}: gate candidate "
            f"{index + 1}/{total_candidates} start ({len(members)} members)"
        )
        try:
            return _call_gate(
                client,
                cluster_id,
                candidate_id,
                members,
                candidate_units,
                by_id,
                _evidence_text(cand),
                out_dir,
                cfg,
            ), None
        except Exception as exc:  # noqa: BLE001 - failed candidates go to noise
            return None, exc

    if gate_workers == 1:
        outcomes = [
            call_candidate(index, cand)
            for index, cand in enumerate(candidates_doc["candidates"])
        ]
    else:
        status(
            f"signal cluster {cluster_id}: gating {total_candidates} candidates "
            f"with {gate_workers} workers"
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=gate_workers) as executor:
            futures = [
                executor.submit(call_candidate, index, cand)
                for index, cand in enumerate(candidates_doc["candidates"])
            ]
            outcomes = [future.result() for future in futures]

    for index, cand in enumerate(candidates_doc["candidates"]):
        candidate_id = f"c{index}"
        members = cand["members"]
        candidate_units = cand.get("units", [])
        decision, gate_error = outcomes[index]
        if gate_error is not None:
            exc = gate_error
            rationale.append(f"{candidate_id}: gate error -> noise ({exc})")
            noise.extend(members)
            status(f"signal cluster {cluster_id}: gate candidate {index + 1}/{total_candidates} failed")
            continue
        assert decision is not None
        groups = decision.get("groups", [])
        if not groups:
            rationale.append(f"{candidate_id}: rejected")
            noise.extend(members)
            status(f"signal cluster {cluster_id}: gate candidate {index + 1}/{total_candidates} rejected")
            continue
        kept: set[str] = set()
        for group in groups:
            gmembers = sorted(dict.fromkeys(group["members"]))
            group_set = set(gmembers)
            units_for_group: dict[str, list[str]] = {member: [] for member in gmembers}
            for item in candidate_units:
                if item.get("file_id") not in group_set:
                    continue
                unit_id = item.get("unit_id")
                if not isinstance(unit_id, str):
                    continue
                for evidence_items in cand.get("evidence", {}).values():
                    if any(
                        record.get("unit_a") == unit_id
                        and record.get("file_a") == item.get("file_id")
                        and record.get("file_b") in group_set
                        for record in evidence_items
                    ) or any(
                        record.get("unit_b") == unit_id
                        and record.get("file_b") == item.get("file_id")
                        and record.get("file_a") in group_set
                        for record in evidence_items
                    ):
                        units_for_group[item["file_id"]].append(unit_id)
                        break
            units_for_group = {file_id: sorted(set(unit_ids)) for file_id, unit_ids in units_for_group.items() if unit_ids}
            group_evidence = {
                signal: [
                    record
                    for record in records
                    if record.get("file_a") in group_set
                    and record.get("file_b") in group_set
                    and (record.get("file_a"), record.get("unit_a"))
                    in {(file_id, unit_id) for file_id, unit_ids in units_for_group.items() for unit_id in unit_ids}
                    and (record.get("file_b"), record.get("unit_b"))
                    in {(file_id, unit_id) for file_id, unit_ids in units_for_group.items() for unit_id in unit_ids}
                ]
                for signal, records in cand.get("evidence", {}).items()
            }
            group_screen = _group_screen(
                gmembers,
                units_for_group,
                group_evidence,
                {
                    key: cfg[key]
                    for key in DEFAULT_EXTRACTION_SCREEN
                    if key in cfg
                },
            )
            clusters.append({
                "cluster_id": f"sub_{len(clusters)}",
                "members": gmembers,
                "shared_concept": group.get("shared_concept", ""),
                "shared_interface": group.get("shared_interface", []),
                "key_variations": group.get("key_variations", []),
                "units": units_for_group,
                "screening": group_screen,
            })
            kept |= set(gmembers)
        dropped = [m for m in members if m not in kept]
        if dropped:
            rationale.append(f"{candidate_id}: dropped {sorted(dropped)} -> noise")
            noise.extend(dropped)
        status(
            f"signal cluster {cluster_id}: gate candidate "
            f"{index + 1}/{total_candidates} done ({len(groups)} groups)"
        )
    llm_clusters = list(clusters)
    clusters, scope_audit = _normalize_scopes(clusters)
    # Economic filtering is intentionally delegated to the LLM gate prompt.
    # Keep the old evidence fields as diagnostics, but do not use the
    # deterministic token estimate to remove an LLM-accepted group here.
    extraction_screen = {
        "enabled": False,
        "before_count": len(clusters),
        "after_count": len(clusters),
        "dropped_count": 0,
        "dropped": [],
        "note": "Deterministic pre-extraction economic filtering disabled; LLM gate decides acceptance.",
    }
    covered = {member for cluster_item in clusters for member in cluster_item["members"]}
    noise = sorted(set(all_files) - covered)
    final = {
        "clusters": clusters,
        "noise": noise,
        "rationale": "\n".join(rationale),
        "scope_audit": scope_audit,
        "extraction_screen": extraction_screen,
    }
    write_json(
        out_dir / "gate_summary.json",
        {
            "n_candidates": total_candidates,
            "n_llm_accepted": len(llm_clusters),
            "n_accepted": len(clusters),
            "rationale": rationale,
            "scope_audit": scope_audit,
            "extraction_screen": extraction_screen,
        },
    )
    return final
