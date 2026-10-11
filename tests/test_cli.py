from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import hw2
from hw2_engine import CATALOG, DIRECTORIES, extract_submission


REMOTE = "https://github.com/student/missing-semester-hw2.git"


class StudentCliTests(unittest.TestCase):
    @staticmethod
    def configure_origin(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", REMOTE], check=True, capture_output=True)

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = tempfile.TemporaryDirectory(prefix="hw2-cli-fixture-")
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.fixture_root = Path(cls.fixture.name)
        cls.configure_origin(cls.fixture_root)
        with redirect_stdout(io.StringIO()):
            result = hw2.main(["init"], root=cls.fixture_root)
        if result != 0:
            raise AssertionError("Could not create the CLI fixture")

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="hw2-cli-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(self.fixture_root / "workspace", self.root / "workspace")
        shutil.copytree(self.fixture_root / ".hw2", self.root / ".hw2")
        self.configure_origin(self.root)

    def invoke(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output):
            result = hw2.main(list(arguments), root=self.root)
        return result, output.getvalue()

    def test_init_is_idempotent_and_recovers_ignored_workspace(self) -> None:
        manifest = self.root / ".hw2/manifest.json"
        before = manifest.read_bytes()
        task = self.root / "workspace" / DIRECTORIES[1]
        marker = task / "student-notes.txt"
        marker.write_text("Keep this work\n")
        result, feedback = self.invoke("init")
        self.assertEqual(result, 0)
        self.assertIn("уже созданы", feedback)
        self.assertEqual(marker.read_text(), "Keep this work\n")
        self.assertEqual(manifest.read_bytes(), before)
        shutil.rmtree(self.root / "workspace")
        result, feedback = self.invoke("init")
        self.assertEqual(result, 0)
        self.assertIn("Готово: 32 задачи", feedback)
        self.assertNotIn("Восстановлено отправок", feedback)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertFalse(marker.exists())
        self.assertEqual(len([path for path in (self.root / "workspace").iterdir() if path.is_dir()]), 32)
        data = json.loads(before)
        self.assertEqual(data["schema"], hw2.SCHEMA)
        self.assertEqual(data["repository"], "student/missing-semester-hw2")
        self.assertEqual(data["seed"], hw2.seed_for_repository(data["repository"]))

    def test_check_uses_a_copy_and_submit_keeps_complete_repository(self) -> None:
        task = self.root / "workspace" / DIRECTORIES[1]
        config = task / ".git/config"
        before = config.read_bytes()
        result, feedback = self.invoke("check", "3")
        self.assertEqual(result, 1)
        self.assertIn("03:", feedback)
        self.assertIn("пока не зачтено", feedback)
        self.assertEqual(config.read_bytes(), before)
        result, _ = self.invoke("submit", "3")
        self.assertEqual(result, 0)
        self.assertEqual(config.read_bytes(), before)
        archive_path = self.root / "submissions" / f"{DIRECTORIES[1]}.tar"
        with tarfile.open(archive_path, "r:") as archive:
            names = set(archive.getnames())
        self.assertIn(f"{DIRECTORIES[1]}/.git/config", names)
        self.assertIn(f"{DIRECTORIES[1]}/note.txt", names)

    def test_submitting_invalid_files_preserves_last_good_archive(self) -> None:
        result, _ = self.invoke("submit", "3")
        self.assertEqual(result, 0)
        archive_path = self.root / "submissions" / f"{DIRECTORIES[1]}.tar"
        before = archive_path.read_bytes()
        outside = self.root / "outside.txt"
        outside.write_text("outside\n")
        (self.root / "workspace" / DIRECTORIES[1] / "outside-link").symlink_to(outside)
        result, feedback = self.invoke("submit", "3")
        self.assertEqual(result, 2)
        self.assertIn("не поддерживаются", feedback)
        self.assertEqual(archive_path.read_bytes(), before)

    def test_failed_resubmission_needs_force_and_saved_work_is_restored(self) -> None:
        task = self.root / "workspace" / DIRECTORIES[1]
        note = task / "student-notes.txt"
        note.write_text("first attempt\n")
        result, _ = self.invoke("submit", "3")
        self.assertEqual(result, 0)
        archive_path = self.root / "submissions" / f"{DIRECTORIES[1]}.tar"
        before = archive_path.read_bytes()
        note.write_text("second attempt\n")
        result, feedback = self.invoke("submit", "3")
        self.assertEqual(result, 2)
        self.assertIn("--force", feedback)
        self.assertEqual(archive_path.read_bytes(), before)
        result, _ = self.invoke("submit", "3", "--force")
        self.assertEqual(result, 0)
        self.assertNotEqual(archive_path.read_bytes(), before)
        shutil.rmtree(self.root / "workspace")
        result, feedback = self.invoke("init")
        self.assertEqual(result, 0)
        self.assertIn("Восстановлено отправок: 1", feedback)
        self.assertEqual(note.read_text(), "second attempt\n")

    def test_malformed_saved_archive_stops_restore_without_losing_archive(self) -> None:
        result, _ = self.invoke("submit", "3")
        self.assertEqual(result, 0)
        archive_path = self.root / "submissions" / f"{DIRECTORIES[1]}.tar"
        archive_path.write_bytes(b"not a tar archive")
        shutil.rmtree(self.root / "workspace")
        result, feedback = self.invoke("init")
        self.assertEqual(result, 2)
        self.assertIn("повреждён", feedback)
        self.assertFalse((self.root / "workspace").exists())
        self.assertEqual(archive_path.read_bytes(), b"not a tar archive")

    def test_checker_never_prints_engine_feedback(self) -> None:
        with patch.object(hw2, "check", return_value=(False, "Correct SHA is secret")):
            result, feedback = self.invoke("check", "3")
        self.assertEqual(result, 1)
        self.assertIn("03:", feedback)
        self.assertIn("пока не зачтено", feedback)
        self.assertNotIn("secret", feedback)

    def test_display_number_one_is_first_catalog_task(self) -> None:
        first = CATALOG[0]
        self.assertEqual(first["path"], "01_setup")
        self.assertEqual(hw2._task_number("1"), first["id"])
        result, feedback = self.invoke("check", "1")
        self.assertEqual(result, 1)
        self.assertIn("01:", feedback)
        result, feedback = self.invoke("submit", "1")
        self.assertEqual(result, 0)
        self.assertIn("submissions/01_setup.tar", feedback)

    def test_first_task_student_check_submit_and_restore(self) -> None:
        repo = self.root / "workspace/01_setup/repo"
        for arguments in (
            ("config", "--local", "user.name", "Student"),
            ("config", "--local", "user.email", "student@example.invalid"),
            ("add", "project.txt"),
            ("commit", "-m", "Initial project"),
        ):
            subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)
        result, feedback = self.invoke("check", "1")
        self.assertEqual(result, 0)
        self.assertIn("01: зачтено", feedback)
        result, feedback = self.invoke("submit", "1")
        self.assertEqual(result, 0)
        self.assertIn("01: зачтено", feedback)
        shutil.rmtree(self.root / "workspace")
        result, _ = self.invoke("init")
        self.assertEqual(result, 0)
        result, feedback = self.invoke("check", "1")
        self.assertEqual(result, 0)
        self.assertIn("01: зачтено", feedback)

    def test_local_clone_with_hardlinked_objects_is_accepted(self) -> None:
        task = self.root / "workspace/18_clone"
        subprocess.run(["git", "clone", "-q", "remote.git", "repo"], cwd=task, check=True, capture_output=True)
        result, feedback = self.invoke("check", "18")
        self.assertEqual(result, 0, feedback)
        self.assertIn("18: зачтено", feedback)

    def test_all_initial_tasks_fit_safe_archive_and_roundtrip(self) -> None:
        for task in CATALOG:
            with self.subTest(task=task["path"]):
                payload = hw2._archive(self.root / "workspace" / task["path"])
                with tempfile.TemporaryDirectory(prefix="hw2-cli-roundtrip-") as temporary:
                    destination = Path(temporary)
                    extract_submission(payload, destination)
                    self.assertTrue((destination / task["path"]).is_dir())

    def test_check_all_reports_display_order_and_failed_status(self) -> None:
        with patch.object(hw2, "_package_and_check", return_value=(b"", False)):
            result, feedback = self.invoke("check", "all")
        self.assertEqual(result, 1)
        lines = feedback.splitlines()
        self.assertEqual(len(lines), 32)
        self.assertTrue(lines[0].startswith("01:"))
        self.assertTrue(lines[-1].startswith("32:"))

    def test_orphan_workspace_is_not_overwritten(self) -> None:
        (self.root / ".hw2/manifest.json").unlink()
        result, feedback = self.invoke("init")
        self.assertEqual(result, 2)
        self.assertIn("не изменены", feedback)
        self.assertTrue((self.root / "workspace" / DIRECTORIES[1]).exists())

    def test_origin_mismatch_rejects_check_submit_and_init(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), "remote", "set-url", "origin", "https://github.com/another-student/homework.git"],
            check=True,
            capture_output=True,
        )
        for command in (("check", "3"), ("submit", "3"), ("init",)):
            with self.subTest(command=command):
                result, feedback = self.invoke(*command)
                self.assertEqual(result, 2)
                self.assertIn("не соответствует origin", feedback)
        self.assertFalse((self.root / "submissions").exists())

    def test_remote_error_does_not_print_credentials(self) -> None:
        marker = "top-secret-token"
        subprocess.run(
            ["git", "-C", str(self.root), "remote", "set-url", "origin", f"https://{marker}@github.com/another-student/homework.git"],
            check=True,
            capture_output=True,
        )
        result, feedback = self.invoke("check", "3")
        self.assertEqual(result, 2)
        self.assertNotIn(marker, feedback)

    def test_tampered_seed_is_rejected(self) -> None:
        manifest = self.root / ".hw2/manifest.json"
        data = json.loads(manifest.read_text())
        data["seed"] = "0" * 32
        manifest.write_text(json.dumps(data))
        result, feedback = self.invoke("check", "3")
        self.assertEqual(result, 2)
        self.assertIn("не соответствует origin", feedback)

    def test_init_without_origin_does_not_create_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hw2-cli-no-origin-") as temporary:
            root = Path(temporary)
            output = io.StringIO()
            with redirect_stdout(output):
                result = hw2.main(["init"], root=root)
            self.assertEqual(result, 2)
            self.assertIn("origin", output.getvalue())
            self.assertFalse((root / "workspace").exists())
            self.assertFalse((root / ".hw2").exists())


if __name__ == "__main__":
    unittest.main()
