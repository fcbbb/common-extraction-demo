"""LLM judgment gate: per-candidate DeepSeek call validating tool-discovered groups.

Accepts/rejects each candidate cluster, may drop members, and names the shared
interface.  Produces the final discovery.json in the baseline_b schema:
  {clusters: [{cluster_id, members, shared_concept, shared_interface, key_variations}],
   noise: [...], rationale: "..."}
"""
from __future__ import annotations

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
    write_json,
)

SYSTEM_PATH = PROMPT_DIR / "signal_gate_system.txt"
USER_PATH = PROMPT_DIR / "signal_gate_user_template.txt"


def _evidence_text(candidate: dict[str, Any]) -> str:
    lines = []
    ev = candidate.get("evidence", {})
    for sig, items in ev.items():
        for it in items:
            if sig == "semantic":
                lines.append(f"semantic: {it['file_a']}~{it['file_b']} cosine={it['cosine']}")
            else:
                lines.append(
                    f"{sig}: {it['file_a']}~{it['file_b']} shared={it.get('shared_tokens')} tokens "
                    f"containment={it.get('containment')} runs={it.get('n_runs')}"
                )
    return "\n".join(lines) if lines else "(no pair evidence recorded)"


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
        if seen & set(sub):
            raise ValueError("groups must be disjoint")
        seen |= set(sub)


def _call_gate(
    client: DeepSeekClient,
    cluster_id: str,
    candidate_id: str,
    members: list[str],
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
    for index, cand in enumerate(candidates_doc["candidates"]):
        candidate_id = f"c{index}"
        members = cand["members"]
        try:
            decision = _call_gate(client, cluster_id, candidate_id, members, by_id, _evidence_text(cand), out_dir, cfg)
        except Exception as exc:  # noqa: BLE001 - failed candidates go to noise
            rationale.append(f"{candidate_id}: gate error -> noise ({exc})")
            noise.extend(members)
            continue
        groups = decision.get("groups", [])
        if not groups:
            rationale.append(f"{candidate_id}: rejected")
            noise.extend(members)
            continue
        kept: set[str] = set()
        for group in groups:
            gmembers = sorted(dict.fromkeys(group["members"]))
            clusters.append({
                "cluster_id": f"sub_{len(clusters)}",
                "members": gmembers,
                "shared_concept": group.get("shared_concept", ""),
                "shared_interface": group.get("shared_interface", []),
                "key_variations": group.get("key_variations", []),
            })
            kept |= set(gmembers)
        dropped = [m for m in members if m not in kept]
        if dropped:
            rationale.append(f"{candidate_id}: dropped {sorted(dropped)} -> noise")
            noise.extend(dropped)
    noise = sorted(set(noise) | (set(all_files) - {m for c in clusters for m in c["members"]}))
    final = {"clusters": clusters, "noise": noise, "rationale": "\n".join(rationale)}
    write_json(out_dir / "gate_summary.json", {"n_accepted": len(clusters), "rationale": rationale})
    return final
