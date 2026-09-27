from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from demo.downstream.core import _copy_signal_context
from demo.discovery.discover_cluster import discover_cluster
from demo.downstream.signal_context import (
    _prompt_invalidation,
    _safe_stem,
    _semantic_dataset_key,
    _skill_markdown,
    _validate_common_reference,
    _validate_context_common,
)


class DownstreamSignalContextTests(unittest.TestCase):
    def test_context_pack_is_copied_as_agent_visible_subset(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "context"
            (source / "patterns").mkdir(parents=True)
            (source / "snippets").mkdir()
            (source / "manifest.json").write_text(json.dumps({
                "schema": "downstream-signal-context-v1",
                "future_commit": "20afea38984d543247f9aaf9f82095bb927944db",
            }), encoding="utf-8")
            (source / "SKILL.md").write_text("# guidance\n", encoding="utf-8")
            (source / "patterns" / "p.md").write_text("pattern\n", encoding="utf-8")
            (source / "snippets" / "s.md").write_text("snippet\n", encoding="utf-8")
            (source / "private.raw").write_text("not copied\n", encoding="utf-8")

            destination = _copy_signal_context(source, root / "workspace")

            self.assertTrue((destination / "SKILL.md").exists())
            self.assertTrue((destination / "patterns" / "p.md").exists())
            self.assertTrue((destination / "snippets" / "s.md").exists())
            # The audit manifest carries commit hashes and must stay outside.
            self.assertFalse((destination / "manifest.json").exists())
            self.assertFalse((destination / "private.raw").exists())

    def test_skill_markdown_is_agent_facing(self) -> None:
        markdown = _skill_markdown([{
            "subcluster_id": "0_0",
            "member_paths": ["moto/budgets/models.py", "moto/dsql/models.py"],
            "guidance_path": "patterns/0_0.md",
            "common_path": "patterns/0_0_common.py",
            "snippets_path": "snippets/0_0.md",
        }])
        self.assertIn("# Implementation guidance from comparable services", markdown)
        self.assertIn("moto/budgets/models.py", markdown)
        self.assertIn("All paths below are relative to the repository root.", markdown)
        self.assertIn(".downstream/signal-context/patterns/0_0_common.py", markdown)
        self.assertIn(".downstream/signal-context/snippets/0_0.md", markdown)
        self.assertIn("Compare each pattern with the current task", markdown)
        self.assertIn("do not force unrelated code", markdown)
        # No experiment structure or commit identifiers may leak to the agent.
        self.assertNotIn("future", markdown.lower())
        self.assertNotIn("C0", markdown)
        self.assertNotIn("patch", markdown.lower())
        for forbidden in ("0bcc551e", "20afea38", "0123456789abcdef0123456789abcdef01234567"):
            self.assertNotIn(forbidden, markdown)

    def test_semantic_cache_key_tracks_repository_and_source_content(self) -> None:
        entries = [{"file_id": "file_000.py", "rel_path": "pkg/backend.py"}]
        sources = {"file_000.py": "class Backend: pass\n"}
        key = _semantic_dataset_key("task", "org/repo", "abc123", entries, sources)

        self.assertEqual(key, _semantic_dataset_key("task", "org/repo", "abc123", entries, sources))
        self.assertNotEqual(key, _semantic_dataset_key("task", "other/repo", "abc123", entries, sources))
        self.assertNotEqual(key, _semantic_dataset_key("task", "org/repo", "abc123", entries,
                                                       {"file_000.py": "class Different: pass\n"}))

    def test_safe_stem_sanitizes_and_dedupes(self) -> None:
        used: set[str] = set()
        self.assertEqual(_safe_stem("0_0", 0, used), "0_0")
        self.assertEqual(_safe_stem("../escape/attempt", 1, used), "escape_attempt")
        self.assertEqual(_safe_stem("0_0", 2, used), "0_0_2")
        self.assertEqual(_safe_stem("", 3, used), "sub_3")

    def test_prompt_invalidation_rules(self) -> None:
        current = {
            "gate_system": "g1", "gate_user": "g2",
            "common_system": "c1", "common_user": "c2",
        }
        # No previous meta (fresh or pre-meta work dir): gate provenance unknown.
        self.assertEqual(_prompt_invalidation(None, current), (True, True))
        self.assertEqual(_prompt_invalidation(dict(current), current), (False, False))
        gate_changed = dict(current, gate_system="g3")
        self.assertEqual(_prompt_invalidation(gate_changed, current), (True, True))
        common_changed = dict(current, common_user="c3")
        self.assertEqual(_prompt_invalidation(common_changed, current), (False, True))

    def test_reference_validation_grounds_imports(self) -> None:
        members = {"file_000.py": "import json\n\n\ndef dumps_pair(a, b):\n    return json.dumps([a, b])\n"}
        subcluster = {"cluster_id": "0_0", "members": ["file_000.py"]}
        with TemporaryDirectory() as temp:
            out_dir = Path(temp)

            grounded = {
                "library": {"path": "common.py",
                            "content": "import json\n\n\ndef dumps_pair(a, b):\n    return json.dumps([a, b])\n"},
            }
            self.assertEqual(_validate_common_reference(grounded, subcluster, members, out_dir)["status"], "ok")

            hallucinated = {
                "library": {"path": "common.py",
                            "content": "import orjson\nfrom moto.core.models import MagicBackend\n\n\ndef f():\n    return 1\n"},
            }
            result = _validate_common_reference(hallucinated, subcluster, members, out_dir)
            self.assertEqual(result["status"], "ungrounded")
            self.assertIn("orjson", result["invented_imports"])
            self.assertIn("MagicBackend", result["invented_imports"])

            undefined = {
                "library": {"path": "common.py",
                            "content": "def f():\n    return NotFoundException('missing')\n"},
            }
            result = _validate_common_reference(undefined, subcluster, members, out_dir)
            self.assertEqual(result["status"], "ungrounded")
            self.assertIn("NotFoundException", result["undefined_names"])

            empty = {"library": {"path": "common.py", "content": "import json\n"}}
            self.assertEqual(_validate_common_reference(empty, subcluster, members, out_dir)["status"], "empty")

            broken = {"library": {"path": "common.py", "content": "def (:\n"}}
            self.assertEqual(_validate_common_reference(broken, subcluster, members, out_dir)["status"],
                             "compile_failed")

    def test_reference_validation_allows_stdlib_abstraction(self) -> None:
        # Expressing a shared pattern as an ABC needs abc/typing vocabulary the
        # members never import; that is a generalization, not a hallucination.
        members = {"file_000.py": "class Shape:\n    def area(self):\n        raise NotImplementedError\n"}
        subcluster = {"cluster_id": "0_0", "members": ["file_000.py"]}
        with TemporaryDirectory() as temp:
            out_dir = Path(temp)

            abstract = {
                "library": {"path": "common.py", "content": (
                    "from abc import ABC, abstractmethod\n"
                    "from typing import Optional\n\n\n"
                    "class ShapeBase(ABC):\n"
                    "    def __init__(self, scale: Optional[float] = None):\n"
                    "        self.scale = scale\n\n"
                    "    @abstractmethod\n"
                    "    def area(self):\n"
                    "        ...\n"
                )},
            }
            self.assertEqual(_validate_common_reference(abstract, subcluster, members, out_dir)["status"],
                             "ok")

            # Non-stdlib imports keep requiring member evidence, even when the
            # member does import part of the same third-party package.
            hallucinated = {
                "library": {"path": "common.py", "content": (
                    "from abc import ABC\n"
                    "import orjson\n\n\n"
                    "class ShapeBase(ABC):\n"
                    "    def dumps(self):\n"
                    "        return orjson.dumps(1)\n"
                )},
            }
            result = _validate_common_reference(hallucinated, subcluster, members, out_dir)
            self.assertEqual(result["status"], "ungrounded")
            self.assertIn("orjson", result["invented_imports"])

    def test_discovery_rebuilds_unfingerprinted_resume_cache(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "dataset"
            results = root / "results"
            original = dataset / "clusters" / "0" / "original"
            original.mkdir(parents=True)
            (original / "file_000.py").write_text("class A: pass\n", encoding="utf-8")
            (original / "file_001.py").write_text("class B: pass\n", encoding="utf-8")
            out = results / "signal" / "0"
            out.mkdir(parents=True)
            (out / "discovery_candidates.json").write_text(json.dumps({
                "cluster_id": "0", "n_files": 2, "n_candidates": 0,
                "candidates": [], "noise": [], "edges_total": 0, "edges_kept": 0,
                "config": {},
            }), encoding="utf-8")
            (out / "discovery.json").write_text(json.dumps({
                "clusters": [{"cluster_id": "stale", "members": ["file_000.py"]}],
                "noise": [],
            }), encoding="utf-8")
            cluster = {"cluster_id": "0", "files": [
                {"file_id": "file_000.py"}, {"file_id": "file_001.py"},
            ]}
            fused = {"candidates": [], "noise": [], "edges_total": 0,
                     "edges_kept": 0, "config": {}}
            with patch("demo.discovery.discover_cluster.run_discovery", return_value=([], {})) as signals, \
                 patch("demo.discovery.fusion.fuse_units", return_value=fused), \
                 patch("demo.discovery.gate.run_gate", return_value={"clusters": [], "noise": []}) as gate:
                discover_cluster(
                    cluster, dataset, results, {}, True, False, "model", False, True, {},
                    semantic_dataset_key="source-fingerprint",
                )
            signals.assert_called_once()
            gate.assert_called_once()
            cached = json.loads((out / "discovery_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(cached["semantic_dataset_key"], "source-fingerprint")

    def test_context_common_requires_structured_guidance(self) -> None:
        payload = {
            "library": {"path": "common.py", "content": "HELPER = 1\n"},
            "rationale": "shared constant",
            "agent_guidance": {
                "summary": "shared constant",
                "invariants": ["same value"],
                "variation_points": [],
                "pitfalls": ["check provider values"],
                "recommended_use": ["use as a reference"],
            },
        }
        _validate_context_common(payload)

        payload["agent_guidance"]["pitfalls"] = "not-a-list"
        with self.assertRaises(ValueError):
            _validate_context_common(payload)


if __name__ == "__main__":
    unittest.main()
