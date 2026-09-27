from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from demo.downstream.core import (
    CommandResult,
    _apply_reference_scores,
    _empty_result,
    _load_future_reference,
    junit_keys,
    run_agent_command,
    strip_classname,
)


SHA = "0" * 40


def _success(command, cwd) -> CommandResult:
    return CommandResult(command=list(command), cwd=str(cwd), returncode=0,
                         timed_out=False, wall_time_sec=0.0,
                         stdout="", stderr="")


def _task() -> dict:
    return {
        "task_id": "demo_future_task",
        "repository": {"slug": "example/demo-repo", "url": "https://example.invalid/demo",
                       "license": "MIT"},
        "history": {"c0": SHA, "future_start": SHA, "future_end": SHA},
        "evolution_pattern": "future-adds-tests",
        "agent_task": "implement the TODO",
        "future_test_paths": ["tests/test_future.py"],
        "regression_test_paths": ["tests/test_old.py"],
        "future_test_command": "python3 -m pytest -q tests",
        "regression_test_command": "python3 -m pytest -q other",
        "status": "ready",
    }


def _write_junit(path: Path, cases: list[tuple[str, str, str]]) -> None:
    """cases: (classname, name, outcome) with outcome in pass/fail/error/skip."""
    tags = {"fail": "failure", "error": "error", "skip": "skipped"}
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name="pytest", tests=str(len(cases)))
    for classname, name, outcome in cases:
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if outcome != "pass":
            ET.SubElement(case, tags[outcome])
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8")


def _write_manifest(path: Path, task: dict) -> None:
    payload = {"schema": "downstream-history-tasks-v1", "selection_status": "test",
               "selection_policy": "test", "tasks": [task]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


REFERENCE_PREFIX = ".cache.history.example.demo-repo"


class StripClassnameTests(unittest.TestCase):
    def test_run_side_classname_is_stripped_at_source_marker(self) -> None:
        self.assertEqual(
            strip_classname(".cache.downstream-workspaces.t.source.tests.test_a.TestX"),
            "tests.test_a.TestX",
        )

    def test_reference_side_classname_is_stripped_at_repo_prefix(self) -> None:
        self.assertEqual(
            strip_classname(f"{REFERENCE_PREFIX}.tests.test_a.TestX", REFERENCE_PREFIX),
            "tests.test_a.TestX",
        )

    def test_unrelated_classname_is_returned_unchanged(self) -> None:
        self.assertEqual(strip_classname("tests.test_a.TestX", REFERENCE_PREFIX),
                         "tests.test_a.TestX")


class JunitKeysTests(unittest.TestCase):
    def test_outcomes_cover_pass_fail_error_and_skip(self) -> None:
        with TemporaryDirectory() as temp:
            junit = Path(temp) / "junit.xml"
            _write_junit(junit, [
                ("x.source.tests.test_a.TestX", "test_pass", "pass"),
                ("x.source.tests.test_a.TestX", "test_fail", "fail"),
                ("x.source.tests.test_a.TestX", "test_error", "error"),
                ("x.source.tests.test_a.TestX", "test_skip", "skip"),
            ])
            outcomes = junit_keys(junit)
        self.assertEqual(outcomes, {
            "tests.test_a.TestX::test_pass": "pass",
            "tests.test_a.TestX::test_fail": "fail",
            "tests.test_a.TestX::test_error": "error",
            "tests.test_a.TestX::test_skip": "skip",
        })


class ApplyReferenceScoresTests(unittest.TestCase):
    def _result(self) -> dict:
        return _empty_result("demo_future_task", "direct", "test")

    def test_agent_added_tests_are_counted_but_never_scored(self) -> None:
        with TemporaryDirectory() as temp:
            junit = Path(temp) / "junit.xml"
            _write_junit(junit, [
                ("w.source.tests.test_a.TestX", "test_1", "pass"),   # reference
                ("w.source.tests.test_a.TestX", "test_2", "fail"),   # reference
                ("w.source.tests.test_agent.Self", "test_own", "pass"),  # agent's own
            ])
            reference = {"tests.test_a.TestX::test_1", "tests.test_a.TestX::test_2"}
            result = self._result()
            green = _apply_reference_scores(result, reference, junit)
        self.assertFalse(green)
        self.assertEqual(result["tests"]["future_passed"], 1)
        self.assertEqual(result["tests"]["future_total"], 2)
        self.assertEqual(result["tests"]["non_reference_tests_collected"], 1)

    def test_green_follows_reference_set_not_collected_counts(self) -> None:
        with TemporaryDirectory() as temp:
            junit = Path(temp) / "junit.xml"
            _write_junit(junit, [
                ("w.source.tests.test_a.TestX", "test_1", "pass"),
                ("w.source.tests.test_agent.Self", "test_own", "fail"),  # failing self-test
            ])
            reference = {"tests.test_a.TestX::test_1"}
            result = self._result()
            green = _apply_reference_scores(result, reference, junit)
        self.assertTrue(green)
        self.assertEqual(result["tests"]["future_passed"], 1)
        self.assertEqual(result["tests"]["future_total"], 1)
        self.assertEqual(result["tests"]["non_reference_tests_collected"], 1)

    def test_missing_reference_tests_count_as_not_passed(self) -> None:
        with TemporaryDirectory() as temp:
            junit = Path(temp) / "junit.xml"
            _write_junit(junit, [("w.source.tests.test_a.TestX", "test_1", "pass")])
            reference = {"tests.test_a.TestX::test_1", "tests.test_a.TestX::test_2"}
            result = self._result()
            green = _apply_reference_scores(result, reference, junit)
        self.assertFalse(green)
        self.assertEqual(result["tests"]["future_passed"], 1)
        self.assertEqual(result["tests"]["future_total"], 2)


class LoadFutureReferenceTests(unittest.TestCase):
    def test_missing_reference_file_degrades_to_none(self) -> None:
        with TemporaryDirectory() as temp:
            self.assertIsNone(_load_future_reference(_task(), Path(temp)))

    def test_none_root_degrades_to_none(self) -> None:
        self.assertIsNone(_load_future_reference(_task(), None))

    def test_reference_keys_are_normalized(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            _write_junit(root / "demo_future_task.xml", [
                (f"{REFERENCE_PREFIX}.tests.test_a.TestX", "test_1", "pass"),
            ])
            reference = _load_future_reference(_task(), root)
        self.assertEqual(reference, {"tests.test_a.TestX::test_1"})


class RunAgentCommandScoringTests(unittest.TestCase):
    """End-to-end: patched commands, fabricated junits, reference scoring in run.json."""

    def _run(self, future_cases, regression_cases, *, references_root) -> dict:
        task = _task()
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "run" / "source"
            workspace.mkdir(parents=True)
            result_dir = root / "result"
            manifest = root / "tasks.json"
            _write_manifest(manifest, task)

            def fake_run_command(command, cwd, timeout_sec, *, env=None,
                                 artifact_dir=None, stream=False):
                if artifact_dir is not None and artifact_dir.name in ("future", "regression"):
                    cases = future_cases if artifact_dir.name == "future" else regression_cases
                    _write_junit(artifact_dir / "junit.xml", cases)
                    return CommandResult(command=list(command), cwd=str(cwd),
                                         returncode=0 if cases else 1, timed_out=False,
                                         wall_time_sec=0.0, stdout="", stderr="")
                return _success(command, cwd)

            with patch("demo.downstream.core.run_command",
                       side_effect=fake_run_command):
                result = run_agent_command(
                    task_id=task["task_id"], variant="direct", workspace=workspace,
                    agent_command=["true"], result_dir=result_dir,
                    manifest=manifest, timeout_sec=60,
                    references_root=references_root,
                )
        return result

    def test_reference_scoring_replaces_collected_counts_in_run_json(self) -> None:
        with TemporaryDirectory() as temp:
            references_root = Path(temp)
            _write_junit(references_root / "demo_future_task.xml", [
                (f"{REFERENCE_PREFIX}.tests.test_a.TestX", "test_1", "pass"),
                (f"{REFERENCE_PREFIX}.tests.test_a.TestX", "test_2", "pass"),
            ])
            result = self._run(
                future_cases=[
                    ("w.source.tests.test_a.TestX", "test_1", "pass"),
                    ("w.source.tests.test_a.TestX", "test_2", "fail"),
                    ("w.source.tests.test_agent.Self", "test_own", "pass"),
                ],
                regression_cases=[("w.source.tests.test_old.TestOld", "test_kept", "pass")],
                references_root=references_root,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["tests"]["future_passed"], 1)
        self.assertEqual(result["tests"]["future_total"], 2)
        self.assertEqual(result["tests"]["future_reference_total"], 2)
        self.assertEqual(result["tests"]["future_collected_passed"], 2)
        self.assertEqual(result["tests"]["future_collected_total"], 3)
        self.assertEqual(result["tests"]["non_reference_tests_collected"], 1)

    def test_failing_agent_self_test_does_not_fail_a_green_reference_run(self) -> None:
        with TemporaryDirectory() as temp:
            references_root = Path(temp)
            _write_junit(references_root / "demo_future_task.xml", [
                (f"{REFERENCE_PREFIX}.tests.test_a.TestX", "test_1", "pass"),
            ])
            result = self._run(
                future_cases=[
                    ("w.source.tests.test_a.TestX", "test_1", "pass"),
                    ("w.source.tests.test_agent.Self", "test_own", "fail"),
                ],
                regression_cases=[("w.source.tests.test_old.TestOld", "test_kept", "pass")],
                references_root=references_root,
            )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["tests"]["future_passed"], 1)
        self.assertEqual(result["tests"]["future_total"], 1)

    def test_task_without_reference_falls_back_to_collected_counts(self) -> None:
        with TemporaryDirectory() as temp:
            result = self._run(
                future_cases=[("w.source.tests.test_a.TestX", "test_1", "pass")],
                regression_cases=[("w.source.tests.test_old.TestOld", "test_kept", "pass")],
                references_root=Path(temp),  # no reference file inside
            )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["tests"]["future_passed"], 1)
        self.assertEqual(result["tests"]["future_total"], 1)
        self.assertIsNone(result["tests"]["future_reference_total"])
        self.assertEqual(result["tests"]["future_collected_passed"], 1)
        self.assertEqual(result["tests"]["future_collected_total"], 1)


if __name__ == "__main__":
    unittest.main()
