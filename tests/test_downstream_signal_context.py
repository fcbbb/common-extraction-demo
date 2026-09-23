from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from demo.downstream.core import _copy_signal_context
from demo.downstream.signal_context import (
    _prompt_invalidation,
    _safe_stem,
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
        # No experiment structure or commit identifiers may leak to the agent.
        self.assertNotIn("future", markdown.lower())
        self.assertNotIn("C0", markdown)
        self.assertNotIn("patch", markdown.lower())
        for forbidden in ("0bcc551e", "20afea38", "0123456789abcdef0123456789abcdef01234567"):
            self.assertNotIn(forbidden, markdown)

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

            empty = {"library": {"path": "common.py", "content": "import json\n"}}
            self.assertEqual(_validate_common_reference(empty, subcluster, members, out_dir)["status"], "empty")

            broken = {"library": {"path": "common.py", "content": "def (:\n"}}
            self.assertEqual(_validate_common_reference(broken, subcluster, members, out_dir)["status"],
                             "compile_failed")

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
