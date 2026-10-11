import io
from pathlib import Path
import tarfile
import tempfile
import unittest

import hw2_engine as engine


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="hw2-engine-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.seed = "engine-tests"
        cls.manifests = engine.build(cls.root, cls.seed)

    def test_catalog_and_fixtures_cover_all_tasks(self):
        self.assertEqual(set(engine.DIRECTORIES), set(range(1, 33)))
        self.assertEqual(set(self.manifests), set(engine.DIRECTORIES))
        self.assertEqual(
            {path.name for path in self.root.iterdir()},
            set(engine.DIRECTORIES.values()),
        )

    def test_repository_identity_accepts_only_supported_github_remotes(self):
        expected = "nikfilk2030/missing_semester_26_hw_2"
        for remote in (
            "https://github.com/Nikfilk2030/missing_semester_26_hw_2",
            "https://github.com/Nikfilk2030/missing_semester_26_hw_2.git",
            "git@github.com:Nikfilk2030/missing_semester_26_hw_2.git",
            "ssh://git@github.com/Nikfilk2030/missing_semester_26_hw_2.git",
        ):
            with self.subTest(remote=remote):
                self.assertEqual(engine.repository_slug(remote), expected)

    def test_repository_identity_rejects_unsafe_remotes(self):
        remotes = (
            "nikfilk2030/missing_semester_26_hw_2",
            "https://secret@github.com/owner/repo",
            "https://user:secret@github.com/owner/repo",
            "https://github.com/owner/repo?token=secret",
            "https://github.com/owner/repo#secret",
            "https://github.com/owner/repo/extra",
            "https://github.com/owner/../repo",
            "https://github.com/owner/%2e%2e",
            "https://github.com/owner/repo/",
            "https://github.com.evil.example/owner/repo",
            "https://gİthub.com/owner/repo",
            "https://gitlab.com/owner/repo",
            "ssh://other@github.com/owner/repo",
            "ssh://git@github.com:22/owner/repo",
            "git@github.com:owner/repo/extra",
            "git@github.com:owner/../repo",
        )
        for remote in remotes:
            with self.subTest(remote=remote), self.assertRaises(ValueError) as raised:
                engine.repository_slug(remote)
            self.assertNotIn("secret", str(raised.exception))

    def test_repository_seed_is_stable_and_requires_canonical_slug(self):
        slug = "nikfilk2030/missing_semester_26_hw_2"
        self.assertEqual(engine.seed_for_repository(slug), "3b4aff33a32498683f407806842152cf")
        self.assertNotEqual(engine.seed_for_repository(slug), engine.seed_for_repository("nikfilk2030/other"))
        for invalid in ("Nikfilk2030/missing_semester_26_hw_2", "owner/..", "owner/repo/extra", "owner//repo", "owner/репозиторий", "", None):
            with self.subTest(slug=invalid), self.assertRaises(ValueError):
                engine.seed_for_repository(invalid)

    def test_check_does_not_change_student_repository(self):
        task = self.root / engine.DIRECTORIES[1]
        config = task / ".git/config"
        index = task / ".git/index"
        before = config.read_bytes(), index.read_bytes()
        passed, _ = engine.check(task, self.seed, 1)
        self.assertFalse(passed)
        self.assertEqual((config.read_bytes(), index.read_bytes()), before)

    def test_check_rejects_symlink_without_following_it(self):
        task = self.root / engine.DIRECTORIES[1]
        outside = self.root / "outside.txt"
        outside.write_text("keep\n")
        link = task / "outside-link"
        link.symlink_to(outside)
        try:
            passed, _ = engine.check(task, self.seed, 1)
            self.assertFalse(passed)
            self.assertEqual(outside.read_text(), "keep\n")
        finally:
            link.unlink()
            outside.unlink()

    def test_origin_requires_sibling_remote_without_extra_traversal(self):
        task = self.root / engine.DIRECTORIES[20]
        self.assertTrue(engine.clone_origin_is_sibling("../remote.git", task))
        self.assertTrue(engine.clone_origin_is_sibling("/workspace/18_clone/./remote.git/", task))
        self.assertTrue(engine.clone_origin_is_sibling(f"{task}/remote.git", task))
        self.assertFalse(engine.clone_origin_is_sibling("../other.git", task))
        self.assertFalse(engine.clone_origin_is_sibling("/workspace/18_clone/missing/../remote.git", task))
        self.assertFalse(engine.clone_origin_is_sibling("/workspace/other_task/remote.git", task))

    def test_fixture_archive_can_be_extracted(self):
        archive = engine.fixture(self.seed, 20)
        self.assertLessEqual(len(archive), engine.MAX_ARCHIVE)
        with tempfile.TemporaryDirectory(prefix="hw2-extract-") as temp:
            target = Path(temp)
            engine.extract_submission(archive, target)
            self.assertTrue((target / engine.DIRECTORIES[20] / "remote.git").is_dir())

    def test_archive_rejects_parent_paths_and_symlinks(self):
        for member in ("../outside.txt", "link"):
            with self.subTest(member=member):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    info = tarfile.TarInfo(member)
                    if member == "link":
                        info.type = tarfile.SYMTYPE
                        info.linkname = "outside.txt"
                        archive.addfile(info)
                    else:
                        info.size = 4
                        archive.addfile(info, io.BytesIO(b"bad\n"))
                with tempfile.TemporaryDirectory(prefix="hw2-extract-") as temp:
                    with self.assertRaises(ValueError):
                        engine.extract_submission(stream.getvalue(), Path(temp))


if __name__ == "__main__":
    unittest.main()
