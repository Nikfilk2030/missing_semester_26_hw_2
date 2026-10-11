import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_catalog_and_conditions_cover_32_tasks(self):
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        conditions = (ROOT / "TASKS.md").read_text(encoding="utf-8")
        self.assertEqual(len(catalog), 32)
        self.assertEqual(len({item["id"] for item in catalog}), 32)
        self.assertEqual(len({item["path"] for item in catalog}), 32)
        self.assertTrue(all("hint" not in item for item in catalog))
        self.assertTrue(all(set(item) <= {"id", "path", "revision", "difficulty", "points"} for item in catalog))
        for number, item in enumerate(catalog, 1):
            self.assertTrue(item["path"].startswith(f"{number:02d}_"))
            self.assertIn(f'<a id="task-{number:02d}"></a>', conditions)
            self.assertIn(f"Рабочая папка: `workspace/{item['path']}`.", conditions)

    def test_teacher_answers_are_not_in_public_tree(self):
        forbidden = {"TEACHER_CHEATSHEET.md", "git_solutions.py", "answers.json"}
        source_paths = [path for path in ROOT.iterdir() if path.name not in {".git", "workspace", "submissions", ".hw2"}]
        public_files = {source.name for source in source_paths if source.is_file()}
        public_files.update(path.name for source in source_paths if source.is_dir() for path in source.rglob("*") if path.is_file())
        self.assertFalse(forbidden & public_files)
        self.assertIn("workspace/", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
