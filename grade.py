from __future__ import annotations

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile

from hw2_engine import CATALOG, DIRECTORIES, MAX_ARCHIVE, check, extract_submission, seed_for_repository


SCHEMA = "git-hw2-2"
MAX_MANIFEST = 4096


class InvalidSubmission(ValueError):
    pass


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _canonical_repository(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidSubmission("Укажите идентификатор репозитория OWNER/REPO.")
    canonical = value.lower()
    try:
        seed_for_repository(canonical)
    except ValueError as error:
        raise InvalidSubmission("Укажите идентификатор репозитория OWNER/REPO.") from error
    return canonical


def read_seed(student: Path, repository: str) -> str:
    manifest = student / ".hw2" / "manifest.json"
    if not _directory(student) or not _directory(student / ".hw2") or not _regular(manifest):
        raise InvalidSubmission("Не найден обычный файл .hw2/manifest.json.")
    try:
        manifest_size = manifest.stat().st_size
    except OSError as error:
        raise InvalidSubmission("Манифест повреждён.") from error
    if manifest_size > MAX_MANIFEST:
        raise InvalidSubmission("Манифест слишком большой.")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InvalidSubmission("Манифест повреждён.") from error
    expected_seed = seed_for_repository(repository)
    if not isinstance(data, dict) or set(data) != {"schema", "repository", "seed"} or data["schema"] != SCHEMA or data["repository"] != repository or data["seed"] != expected_seed:
        raise InvalidSubmission("Манифест повреждён или имеет неизвестную версию.")
    return expected_seed


def _read_archive(student: Path, directory: str) -> bytes | None:
    submissions = student / "submissions"
    if not submissions.exists() and not submissions.is_symlink():
        return None
    if not _directory(submissions):
        raise InvalidSubmission("Каталог submissions повреждён.")
    path = submissions / f"{directory}.tar"
    if not path.exists() and not path.is_symlink():
        return None
    if not _regular(path):
        raise InvalidSubmission("Архив должен быть обычным файлом.")
    if path.stat().st_size > MAX_ARCHIVE:
        raise InvalidSubmission("Архив превышает лимит 16 МБ.")
    payload = path.read_bytes()
    if len(payload) > MAX_ARCHIVE:
        raise InvalidSubmission("Архив превышает лимит 16 МБ.")
    return payload


def _evaluate(payload: bytes, directory: str, seed: str, task_id: int) -> bool:
    with tempfile.TemporaryDirectory(prefix="hw2-grade-") as temporary:
        root = Path(temporary)
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
                for member in archive:
                    parts = PurePosixPath(member.name).parts
                    if not parts or parts[0] != directory:
                        raise InvalidSubmission("Архив содержит файлы вне своей задачи.")
            extract_submission(payload, root)
            if {item.name for item in root.iterdir()} != {directory} or not _directory(root / directory):
                raise InvalidSubmission("Архив не содержит ожидаемую папку задачи.")
            return check(root / directory, seed, task_id)[0]
        except Exception as error:
            raise InvalidSubmission("Не удалось безопасно прочитать архив.") from error


def grade(student: Path, repository: str) -> dict:
    canonical = _canonical_repository(repository)
    seed = read_seed(student, canonical)
    results = []
    for number, task in enumerate(CATALOG, start=1):
        task_id = task["id"]
        directory = DIRECTORIES[task_id]
        points = task.get("points", 100)
        status = "не отправлено"
        try:
            payload = _read_archive(student, directory)
            if payload is not None:
                status = "зачтено" if _evaluate(payload, directory, seed, task_id) else "не зачтено"
        except (InvalidSubmission, OSError):
            status = "архив повреждён"
        results.append({"number": number, "path": directory, "status": status, "points": points if status == "зачтено" else 0, "maximum": points})
    return {"earned_points": sum(item["points"] for item in results), "maximum_points": sum(item["maximum"] for item in results), "tasks": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить архивы студента доверенной копией чекера. Запускайте из изолированного контейнера без сети.")
    parser.add_argument("student_checkout", type=Path, help="локальный checkout репозитория студента")
    parser.add_argument("--repository", required=True, metavar="OWNER/REPO", help="репозиторий из доверенной ссылки на GitHub, не из файлов студента")
    parser.add_argument("--json", action="store_true", help="вывести машиночитаемый результат")
    arguments = parser.parse_args(argv)
    try:
        report = grade(arguments.student_checkout, arguments.repository)
    except InvalidSubmission as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for item in report["tasks"]:
            print(f"{item['number']:02d} {item['path']}: {item['status']} ({item['points']}/{item['maximum']})")
        print(f"Итого: {report['earned_points']}/{report['maximum_points']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
