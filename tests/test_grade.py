from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import grade
import hw2
from hw2_engine import CATALOG, build, git, seed_for_repository


class GraderTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="hw2-grader-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.student = self.root / "student"
        self.repository = "student/hw2"
        self.seed = seed_for_repository(self.repository)
        (self.student / ".hw2").mkdir(parents=True)
        (self.student / ".hw2" / "manifest.json").write_text(json.dumps({"schema": grade.SCHEMA, "repository": self.repository, "seed": self.seed}))

    def _submission(self, payload: bytes, directory: str = "01_setup") -> Path:
        submissions = self.student / "submissions"
        submissions.mkdir(exist_ok=True)
        archive = submissions / f"{directory}.tar"
        archive.write_bytes(payload)
        return archive

    def test_missing_archives_are_zero_and_catalog_points_are_preserved(self) -> None:
        report = grade.grade(self.student, self.repository)
        self.assertEqual(len(report["tasks"]), 32)
        self.assertEqual(report["earned_points"], 0)
        self.assertEqual(report["maximum_points"], sum(task.get("points", 100) for task in CATALOG))
        self.assertEqual(report["tasks"][0]["path"], "01_setup")
        self.assertEqual(report["tasks"][0]["status"], "не отправлено")

    def test_teacher_grader_uses_archive_not_student_cli(self) -> None:
        source = self.root / "source"
        source.mkdir()
        build(source, self.seed, 6)
        task = source / "01_setup"
        repo = task / "repo"
        git(repo, "config", "user.name", "Student")
        git(repo, "config", "user.email", "student@example.invalid")
        git(repo, "add", "project.txt")
        git(repo, "commit", "-m", "Initial project")
        self._submission(hw2._archive(task))
        (self.student / "hw2.py").write_text("raise AssertionError('student CLI must not run')\n")
        report = grade.grade(self.student, self.repository)
        self.assertEqual(report["tasks"][0]["status"], "зачтено")
        self.assertEqual(report["earned_points"], 100)
        self.assertEqual(report["tasks"][1]["status"], "не отправлено")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(grade.main([str(self.student), "--repository", self.repository, "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["earned_points"], 100)

    def test_archive_escape_and_wrong_task_are_rejected(self) -> None:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo("../outside.txt")
            info.size = 3
            archive.addfile(info, io.BytesIO(b"bad"))
        archive = self._submission(stream.getvalue())
        self.assertEqual(grade.grade(self.student, self.repository)["tasks"][0]["status"], "архив повреждён")
        self.assertFalse((self.root / "outside.txt").exists())
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as package:
            info = tarfile.TarInfo("02_history/readme.txt")
            info.size = 3
            package.addfile(info, io.BytesIO(b"bad"))
        archive.write_bytes(stream.getvalue())
        self.assertEqual(grade.grade(self.student, self.repository)["tasks"][0]["status"], "архив повреждён")

    def test_manifest_and_archive_symlinks_are_rejected(self) -> None:
        manifest = self.student / ".hw2" / "manifest.json"
        manifest.unlink()
        manifest.symlink_to(self.root / "missing")
        with self.assertRaises(grade.InvalidSubmission):
            grade.grade(self.student, self.repository)
        manifest.unlink()
        manifest.write_text(json.dumps({"schema": grade.SCHEMA, "repository": self.repository, "seed": self.seed}))
        archive = self.student / "submissions" / "01_setup.tar"
        archive.parent.mkdir()
        archive.symlink_to(self.root / "missing")
        self.assertEqual(grade.grade(self.student, self.repository)["tasks"][0]["status"], "архив повреждён")

    def test_repository_identity_and_seed_cannot_be_chosen_by_student(self) -> None:
        manifest = self.student / ".hw2" / "manifest.json"
        with self.assertRaises(grade.InvalidSubmission):
            grade.grade(self.student, "another/hw2")
        self.assertEqual(grade.grade(self.student, "STUDENT/HW2")["earned_points"], 0)
        manifest.write_text(json.dumps({"schema": grade.SCHEMA, "repository": self.repository, "seed": "0" * 32}))
        with self.assertRaises(grade.InvalidSubmission):
            grade.grade(self.student, self.repository)
        manifest.write_text(json.dumps({"schema": grade.SCHEMA, "repository": "another/hw2", "seed": seed_for_repository("another/hw2")}))
        with self.assertRaises(grade.InvalidSubmission):
            grade.grade(self.student, self.repository)
        with self.assertRaises(grade.InvalidSubmission):
            grade.grade(self.student, "https://github.com/student/hw2")


if __name__ == "__main__":
    unittest.main()
