from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import hw2
from hw2_engine import CATALOG, DIRECTORIES, extract_submission, git


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
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
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

    def test_successful_check_submit_and_restore(self) -> None:
        with patch.object(hw2, "check", return_value=(True, "")):
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

    def test_hardlinked_files_are_archived_as_regular_files(self) -> None:
        task = self.root / "workspace" / DIRECTORIES[1]
        original = task / "shared-data"
        duplicate = task / "shared-data-copy"
        original.write_bytes(b"test data\n")
        os.link(original, duplicate)
        payload = hw2._archive(task)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            self.assertTrue(archive.getmember(f"{task.name}/{original.name}").isfile())
            self.assertTrue(archive.getmember(f"{task.name}/{duplicate.name}").isfile())
        with tempfile.TemporaryDirectory(prefix="hw2-hardlink-roundtrip-") as temporary:
            destination = Path(temporary)
            extract_submission(payload, destination)
            self.assertEqual((destination / task.name / duplicate.name).read_bytes(), original.read_bytes())

    def test_init_sets_local_author_except_in_first_task(self) -> None:
        self.assertEqual(git(self.fixture_root, "config", "--local", "--get", "user.name"), "Student")
        self.assertEqual(git(self.fixture_root, "config", "--local", "--get", "user.email"), "student@example.invalid")
        repo = self.root / "workspace/03_commit"
        git(repo, "config", "--local", "--unset", "user.name")
        git(repo, "config", "--local", "--unset", "user.email")
        before = self.tree_bytes(self.root / "workspace/03_commit")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
            result, feedback = self.invoke("init")
        self.assertEqual(result, 0, feedback)
        repositories = [self.root]
        for directory in DIRECTORIES.values():
            if directory == CATALOG[0]["path"]:
                continue
            task = self.root / "workspace" / directory
            repositories.extend(path for path in (task, task / "repo") if (path / ".git").is_dir())
        for repository in repositories:
            with self.subTest(repository=repository):
                self.assertEqual(git(repository, "config", "--local", "--get", "user.name"), "Student")
                self.assertEqual(git(repository, "config", "--local", "--get", "user.email"), "student@example.invalid")
        control = self.root / "workspace/01_setup/control"
        self.assertEqual(git(control, "config", "--local", "--get", "user.name"), "Control")
        self.assertEqual(git(control, "config", "--local", "--get", "user.email"), "control@example.invalid")
        after = self.tree_bytes(self.root / "workspace/03_commit")
        self.assertEqual({name: data for name, data in after.items() if name != ".git/config"},
                         {name: data for name, data in before.items() if name != ".git/config"})

    def test_first_task_requires_manual_author_after_init_and_revert(self) -> None:
        repo = self.root / "workspace/01_setup/repo"
        for key in ("user.name", "user.email"):
            self.assertEqual(git(repo, "config", "--local", "--get", key, allow_status1=True), "")
        git(repo, "config", "--local", "user.name", "Manual User")
        git(repo, "config", "--local", "user.email", "manual@example.invalid")
        config_before = (repo / ".git/config").read_bytes()
        result, feedback = self.invoke("init")
        self.assertEqual(result, 0, feedback)
        self.assertEqual((repo / ".git/config").read_bytes(), config_before)
        result, feedback = self.invoke("revert", "1")
        self.assertEqual(result, 0, feedback)
        for key in ("user.name", "user.email"):
            self.assertEqual(git(repo, "config", "--local", "--get", key, allow_status1=True), "")

    def test_init_preserves_personal_root_author(self) -> None:
        global_config = self.root / "global.gitconfig"
        global_config.write_text("[user]\nname = Personal User\nemail = personal@example.invalid\n")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_NOSYSTEM": "1"}):
            for existing_workspace in (True, False):
                with self.subTest(existing_workspace=existing_workspace):
                    if not existing_workspace:
                        shutil.rmtree(self.root / "workspace")
                    result, feedback = self.invoke("init")
                    self.assertEqual(result, 0, feedback)
                    for key in ("user.name", "user.email"):
                        local = subprocess.run(["git", "config", "--local", "--get", key], cwd=self.root, capture_output=True)
                        self.assertEqual(local.returncode, 1)
                    author = subprocess.run(["git", "var", "GIT_AUTHOR_IDENT"], cwd=self.root, capture_output=True, text=True, check=True)
                    self.assertIn("Personal User <personal@example.invalid>", author.stdout)
            git(self.root, "config", "--local", "user.name", "Local User")
            git(self.root, "config", "--local", "user.email", "local@example.invalid")
            result, feedback = self.invoke("init")
            self.assertEqual(result, 0, feedback)
            self.assertEqual(git(self.root, "config", "--local", "--get", "user.name"), "Local User")
            self.assertEqual(git(self.root, "config", "--local", "--get", "user.email"), "local@example.invalid")
        self.assertEqual(global_config.read_text(), "[user]\nname = Personal User\nemail = personal@example.invalid\n")

    def test_revert_restores_local_author_without_global_config(self) -> None:
        repo = self.root / "workspace/03_commit"
        git(repo, "config", "--local", "user.name", "Wrong")
        git(repo, "config", "--local", "user.email", "wrong@example.invalid")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
            result, feedback = self.invoke("revert", "3")
        self.assertEqual(result, 0, feedback)
        self.assertEqual(git(repo, "config", "--local", "--get", "user.name"), "Student")
        self.assertEqual(git(repo, "config", "--local", "--get", "user.email"), "student@example.invalid")

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

    @staticmethod
    def tree_bytes(root: Path) -> dict[str, bytes]:
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    @classmethod
    def fixture_state(cls, task: Path) -> tuple[dict[str, bytes], dict[str, str]]:
        files = {name: content for name, content in cls.tree_bytes(task).items()
                 if name != ".git/index" and not name.endswith("/.git/index") and name != "remote.git/config"}
        indexes = {str(metadata.parent.relative_to(task)): git(metadata.parent, "write-tree")
                   for metadata in task.rglob(".git") if metadata.is_dir()}
        return files, indexes

    def test_revert_recreates_only_selected_task_without_backup(self) -> None:
        task = self.root / "workspace/01_setup"
        original = self.fixture_state(task)
        sibling = self.root / "workspace/03_commit"
        sibling_before = self.tree_bytes(sibling)
        manifest_before = (self.root / ".hw2/manifest.json").read_bytes()
        submissions = self.root / "submissions"
        submissions.mkdir()
        archive = submissions / "01_setup.tar"
        archive.write_bytes(b"previous submission")
        (task / "repo/project.txt").write_text("wrong answer\n")
        (task / "repo/student-notes.txt").write_text("discard this\n")
        shutil.rmtree(task / "repo/.git")
        shutil.rmtree(task / "control")
        result, feedback = self.invoke("revert", "1")
        self.assertEqual(result, 0, feedback)
        self.assertIn("01:", feedback)
        self.assertEqual(self.fixture_state(task), original)
        self.assertEqual(self.tree_bytes(sibling), sibling_before)
        self.assertEqual((self.root / ".hw2/manifest.json").read_bytes(), manifest_before)
        self.assertEqual(archive.read_bytes(), b"previous submission")
        self.assertEqual({path.name for path in (self.root / "workspace").iterdir()}, set(DIRECTORIES.values()))

    def test_revert_handles_legacy_and_missing_tasks(self) -> None:
        for selection, directory in (("3", "03_commit"), ("18", "18_clone")):
            with self.subTest(selection=selection):
                task = self.root / "workspace" / directory
                before = self.fixture_state(task)
                shutil.rmtree(task)
                result, feedback = self.invoke("revert", selection)
                self.assertEqual(result, 0, feedback)
                self.assertEqual(self.fixture_state(task), before)

    def test_revert_invalid_number_leaves_workspace_unchanged(self) -> None:
        before = self.tree_bytes(self.root / "workspace")
        for selection in ("0", "33", "all", "../01_setup"):
            result, _ = self.invoke("revert", selection)
            self.assertEqual(result, 2)
        self.assertEqual(self.tree_bytes(self.root / "workspace"), before)

    def test_revert_generation_failure_preserves_attempt(self) -> None:
        task = self.root / "workspace/01_setup"
        (task / "repo/project.txt").write_text("keep this attempt\n")
        before = self.tree_bytes(task)
        with patch.object(hw2, "build", side_effect=ValueError("fixture failed")):
            result, feedback = self.invoke("revert", "1")
        self.assertEqual(result, 2)
        self.assertIn("не изменены", feedback)
        self.assertEqual(self.tree_bytes(task), before)

    def test_revert_replacement_failure_rolls_back_attempt(self) -> None:
        task = self.root / "workspace/01_setup"
        before = self.tree_bytes(task)
        rename = Path.rename

        def fail_install(path, target):
            if path.parent.name == "fresh":
                raise OSError("cannot install fixture")
            return rename(path, target)

        with patch.object(Path, "rename", fail_install):
            result, _ = self.invoke("revert", "1")
        self.assertEqual(result, 2)
        self.assertEqual(self.tree_bytes(task), before)

    def test_revert_rejects_symlink_task_and_workspace(self) -> None:
        task = self.root / "workspace/01_setup"
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep\n")
        shutil.rmtree(task)
        task.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.invoke("revert", "1")[0], 2)
        self.assertEqual(marker.read_text(), "keep\n")
        task.unlink()
        shutil.rmtree(self.root / "workspace")
        (self.root / "workspace").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.invoke("revert", "1")[0], 2)
        self.assertEqual(marker.read_text(), "keep\n")


if __name__ == "__main__":
    unittest.main()
