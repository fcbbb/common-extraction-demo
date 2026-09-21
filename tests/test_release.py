from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from demo.eval.compare_codecontest_pool import best_disjoint_subclusters
from demo.eval.run_tests import run_python_file
from demo.eval.verify_release import verify


def candidate(name: str, members: list[str], before: int, after: int, status: str = "ok") -> dict:
    return {
        "subcluster_id": name,
        "members": members,
        "status": status,
        "metrics": {"tokens": {"tokens_before": before, "tokens_after": after}},
    }


class DisjointSelectionTests(unittest.TestCase):
    def test_selects_maximum_total_saving_without_member_overlap(self) -> None:
        items = [
            candidate("wide", ["a.py", "b.py"], 100, 50),
            candidate("left", ["a.py"], 60, 20),
            candidate("right", ["b.py"], 60, 20),
            candidate("negative", ["c.py"], 10, 11),
            candidate("failed", ["d.py"], 100, 0, status="tests_failed"),
        ]
        selected = best_disjoint_subclusters(items)
        self.assertEqual([item["subcluster_id"] for item in selected], ["left", "right"])


class ReleaseArtifactTests(unittest.TestCase):
    def test_curated_results_are_self_consistent(self) -> None:
        messages = verify()
        self.assertEqual(len(messages), 3)


class StdioRunnerTests(unittest.TestCase):
    def test_relative_script_path_is_resolved_before_changing_cwd(self) -> None:
        with TemporaryDirectory(dir=".") as tmp:
            script = Path(tmp) / "echo_input.py"
            script.write_text("import sys\nprint(sys.stdin.read().strip())\n", encoding="utf-8")
            result = run_python_file(script, "hello\n", timeout_sec=5)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "hello\n")
        self.assertFalse(result["timed_out"])


if __name__ == "__main__":
    unittest.main()
