from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from demo.downstream.core import (
    CommandResult,
    _run_task_setup,
    _task_env,
    _task_venv_dir,
    run_command,
)


def _success(command, cwd) -> CommandResult:
    return CommandResult(command=list(command), cwd=str(cwd), returncode=0,
                         timed_out=False, wall_time_sec=0.0,
                         stdout="", stderr="")


class TaskEnvTests(unittest.TestCase):
    def test_task_env_prepends_venv_bin_and_keeps_base(self) -> None:
        venv_dir = Path("/tmp/run/.venv")
        env = _task_env(venv_dir, {
            "DOWNSTREAM_TASK_FILE": "/tmp/TASK.md",
            "PATH": "/usr/bin:/bin",
        })
        self.assertEqual(env["DOWNSTREAM_TASK_FILE"], "/tmp/TASK.md")
        self.assertEqual(env["PATH"], f"{venv_dir / 'bin'}{os.pathsep}/usr/bin:/bin")

    def test_task_env_falls_back_to_os_environ_path(self) -> None:
        env = _task_env(Path("/tmp/run/.venv"))
        self.assertTrue(env["PATH"].startswith(f"/tmp/run/.venv/bin{os.pathsep}"))
        self.assertIn(os.environ.get("PATH", ""), env["PATH"])

    def test_task_venv_dir_is_sibling_of_source_checkout(self) -> None:
        workspace = Path("/ws/moto_service_cleanrooms/signal/20260923-201947/source")
        self.assertEqual(
            _task_venv_dir(workspace),
            Path("/ws/moto_service_cleanrooms/signal/20260923-201947/.venv"),
        )


class RunTaskSetupTests(unittest.TestCase):
    def test_venv_is_created_first_and_env_is_forwarded(self) -> None:
        task = {
            "install_command": "python3 -m pip install -e .",
            "test_setup": ["python3 -c pass"],
        }
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "run" / "source"
            workspace.mkdir(parents=True)
            venv_dir = _task_venv_dir(workspace)
            seen = []

            def fake_run_command(command, cwd, timeout_sec, *, env=None,
                                 artifact_dir=None, stream=False):
                seen.append((command, env))
                return _success(command, cwd)

            with patch("demo.downstream.core.run_command",
                       side_effect=fake_run_command):
                records, error = _run_task_setup(
                    task, workspace, root / "result", 60,
                    venv_dir=venv_dir, env=_task_env(venv_dir),
                )

            self.assertIsNone(error)
            self.assertEqual([record["name"] for record in records["commands"]],
                             ["venv", "install", "test_setup_0"])
            self.assertEqual(seen[0][0], [sys.executable, "-m", "venv", str(venv_dir)])
            for _, env in seen:
                self.assertEqual(env["PATH"].split(os.pathsep)[0], str(venv_dir / "bin"))

    def test_venv_failure_reports_error_and_skips_install(self) -> None:
        task = {"install_command": "python3 -m pip install -e ."}
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "run" / "source"
            workspace.mkdir(parents=True)
            venv_dir = _task_venv_dir(workspace)
            seen = []

            def fake_run_command(command, cwd, timeout_sec, *, env=None,
                                 artifact_dir=None, stream=False):
                seen.append(command)
                result = _success(command, cwd)
                if list(command)[1:3] == ["-m", "venv"]:
                    result = CommandResult(
                        command=list(command), cwd=str(cwd), returncode=1,
                        timed_out=False, wall_time_sec=0.0,
                        stdout="", stderr="venv creation failed",
                    )
                return result

            with patch("demo.downstream.core.run_command",
                       side_effect=fake_run_command):
                records, error = _run_task_setup(
                    task, workspace, root / "result", 60,
                    venv_dir=venv_dir, env=None,
                )

            self.assertEqual(error, "venv_failed")
            self.assertEqual([record["name"] for record in records["commands"]],
                             ["venv"])
            self.assertEqual(len(seen), 1)


class VenvResolutionTests(unittest.TestCase):
    def test_child_python_resolves_to_task_venv(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            venv_dir = _task_venv_dir(root / "source")
            created = run_command(
                [sys.executable, "-m", "venv", str(venv_dir)], root, 120,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            result = run_command(
                ["python3", "-c", "import sys; print(sys.prefix)"], root, 60,
                env=_task_env(venv_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip().startswith(str(venv_dir)),
                            result.stdout)


if __name__ == "__main__":
    unittest.main()
