from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile

from hw2_engine import CATALOG, DIRECTORIES, MAX_ARCHIVE, build, check, extract_submission, git, repository_slug, seed_for_repository


SCHEMA = "git-hw2-2"
MAX_MEMBERS = 4096
ROOT = Path(__file__).resolve().parent
NUMBER_TO_ID = {number: item["id"] for number, item in enumerate(CATALOG, start=1)}
ID_TO_NUMBER = {task_id: number for number, task_id in NUMBER_TO_ID.items()}


class CliError(Exception):
    pass


def _manifest(root: Path) -> Path:
    return root / ".hw2" / "manifest.json"


def _repository(root: Path) -> str:
    environment = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            env=environment,
            capture_output=True,
            timeout=4,
        )
        if result.returncode != 0 or len(result.stdout) > 4096:
            raise ValueError
        return repository_slug(result.stdout.decode("utf-8", errors="strict").strip())
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired) as error:
        raise CliError("Для работы нужен origin вашего репозитория GitHub.") from error


def _seed(root: Path) -> str:
    path = _manifest(root)
    if path.parent.is_symlink() or path.is_symlink():
        raise CliError("Манифест не должен быть ссылкой.")
    if not path.is_file():
        raise CliError("Сначала запустите: python3 hw2.py init")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CliError("Файл .hw2/manifest.json повреждён.") from error
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("repository"), str) or not isinstance(data.get("seed"), str):
        raise CliError("Файл .hw2/manifest.json повреждён.")
    repository = _repository(root)
    if data["repository"] != repository or data["seed"] != seed_for_repository(repository):
        raise CliError("Манифест не соответствует origin этого репозитория GitHub.")
    return data["seed"]


def _write_manifest(root: Path, repository: str, seed: str) -> None:
    directory = root / ".hw2"
    if directory.is_symlink():
        raise CliError("Папка .hw2 не должна быть ссылкой.")
    directory.mkdir(exist_ok=True)
    if _manifest(root).exists() or _manifest(root).is_symlink():
        raise CliError("Файл .hw2/manifest.json уже существует.")
    descriptor, name = tempfile.mkstemp(prefix=".manifest-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"schema": SCHEMA, "repository": repository, "seed": seed}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        try:
            os.link(name, _manifest(root))
        except FileExistsError as error:
            raise CliError("Файл .hw2/manifest.json уже существует.") from error
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _restore_submissions(root: Path, staged: Path) -> int:
    submissions = root / "submissions"
    if submissions.is_symlink() or (submissions.exists() and not submissions.is_dir()):
        raise CliError("Папка submissions повреждена; рабочие данные не изменены.")
    if not submissions.exists():
        return 0
    restored_count = 0
    for directory in DIRECTORIES.values():
        archive_path = submissions / f"{directory}.tar"
        if not archive_path.exists() and not archive_path.is_symlink():
            continue
        if archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE:
            raise CliError(f"Архив submissions/{directory}.tar повреждён; рабочие данные не изменены.")
        payload = archive_path.read_bytes()
        if len(payload) > MAX_ARCHIVE:
            raise CliError(f"Архив submissions/{directory}.tar повреждён; рабочие данные не изменены.")
        with tempfile.TemporaryDirectory(prefix=".hw2-restore-", dir=root) as temporary:
            restored_root = Path(temporary)
            try:
                extract_submission(payload, restored_root)
            except (OSError, ValueError, tarfile.TarError) as error:
                raise CliError(f"Архив submissions/{directory}.tar повреждён; рабочие данные не изменены.") from error
            restored = restored_root / directory
            if not restored.is_dir() or {path.name for path in restored_root.iterdir()} != {directory}:
                raise CliError(f"Архив submissions/{directory}.tar повреждён; рабочие данные не изменены.")
            shutil.rmtree(staged / directory)
            restored.rename(staged / directory)
            restored_count += 1
    return restored_count


def _configure_author(repo: Path, *, preserve_existing: bool = False) -> None:
    metadata = repo / ".git"
    if repo.is_symlink() or metadata.is_symlink() or (metadata / "config").is_symlink():
        raise CliError("Репозиторий и его конфигурация не должны быть ссылками.")
    try:
        for key, fallback in (("user.name", "Student"), ("user.email", "student@example.invalid")):
            if preserve_existing:
                result = subprocess.run(
                    ["git", "config", "--get", key], cwd=repo,
                    capture_output=True, text=True, timeout=4,
                )
                if result.returncode not in (0, 1):
                    raise ValueError("Не удалось прочитать настройки автора.")
                if result.returncode == 0 and result.stdout.strip():
                    continue
            git(repo, "config", "--local", key, fallback)
    except (ValueError, subprocess.SubprocessError) as error:
        raise CliError("Не удалось настроить локального автора коммитов.") from error


def _configure_task_author(task: Path) -> None:
    if task.name == CATALOG[0]["path"]:
        return
    for repo in (task, task / "repo"):
        if repo.is_symlink() or (repo / ".git").is_symlink():
            raise CliError("Репозиторий задачи не должен быть ссылкой.")
        if (repo / ".git").is_dir():
            _configure_author(repo)


def init(root: Path) -> None:
    workspace = root / "workspace"
    manifest = _manifest(root)
    if workspace.is_symlink() or manifest.parent.is_symlink() or manifest.is_symlink():
        raise CliError("Рабочая папка или манифест не должны быть ссылками.")
    if workspace.exists():
        if not workspace.is_dir() or not manifest.is_file():
            raise CliError("Папка workspace уже существует; данные не изменены.")
        _seed(root)
        _configure_author(root, preserve_existing=True)
        for directory in DIRECTORIES.values():
            _configure_task_author(workspace / directory)
        print("Задачи уже созданы в workspace/. Локальные настройки автора обновлены; файлы и история сохранены.")
        return
    restoring = manifest.exists()
    repository = _repository(root)
    seed = _seed(root) if restoring else seed_for_repository(repository)
    restored_count = 0
    with tempfile.TemporaryDirectory(prefix=".hw2-init-", dir=root) as temporary:
        staged = Path(temporary) / "workspace"
        staged.mkdir()
        build(staged, seed, None)
        if restoring:
            restored_count = _restore_submissions(root, staged)
        for directory in DIRECTORIES.values():
            _configure_task_author(staged / directory)
        _configure_author(root, preserve_existing=True)
        if not manifest.exists():
            if _repository(root) != repository:
                raise CliError("Origin изменился во время создания задач; данные не изменены.")
            _write_manifest(root, repository, seed)
        staged.rename(workspace)
    print("Готово: 32 задачи в workspace/.")
    if restoring and restored_count:
        print(f"Восстановлено отправок: {restored_count}.")


def _task_number(value: str) -> int:
    try:
        number = int(value, 10)
    except ValueError as error:
        raise CliError("Укажите номер задачи от 1 до 32.") from error
    if number not in NUMBER_TO_ID:
        raise CliError("Укажите номер задачи от 1 до 32.")
    return NUMBER_TO_ID[number]


def _task_directory(root: Path, number: int) -> Path:
    workspace = root / "workspace"
    task = workspace / DIRECTORIES[number]
    if workspace.is_symlink() or not workspace.is_dir() or task.is_symlink() or not task.is_dir():
        raise CliError("Папка задачи не найдена. Запустите: python3 hw2.py init")
    return task


def revert(root: Path, selection: str) -> None:
    number = _task_number(selection)
    seed = _seed(root)
    workspace = root / "workspace"
    task = workspace / DIRECTORIES[number]
    if workspace.is_symlink() or not workspace.is_dir():
        raise CliError("Папка workspace не найдена. Запустите: python3 hw2.py init")
    if task.is_symlink() or (task.exists() and not task.is_dir()):
        raise CliError("Папка задачи должна быть обычным каталогом.")
    with tempfile.TemporaryDirectory(prefix=".hw2-revert-", dir=workspace) as temporary:
        staging = Path(temporary)
        fresh = staging / "fresh"
        fresh.mkdir()
        try:
            build(fresh, seed, number)
        except (ValueError, subprocess.SubprocessError) as error:
            raise CliError("Не удалось создать исходную задачу; рабочие данные не изменены.") from error
        _configure_task_author(fresh / task.name)
        previous = staging / "previous"
        if task.exists():
            task.rename(previous)
        try:
            (fresh / task.name).rename(task)
        except OSError:
            if previous.exists():
                previous.rename(task)
            raise
    print(f"{ID_TO_NUMBER[number]:02d}: задача восстановлена в workspace/{task.name}/. Прежняя попытка удалена.")


def _preflight(task: Path) -> None:
    pending = [task]
    total = 0
    members = 0
    while pending:
        path = pending.pop()
        info = path.lstat()
        members += 1
        if members > MAX_MEMBERS:
            raise CliError("В задаче слишком много файлов.")
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)
        elif stat.S_ISREG(info.st_mode):
            total += info.st_size
            if total > MAX_ARCHIVE:
                raise CliError("Архив задачи превышает лимит 16 МБ.")
        else:
            raise CliError("Ссылки и специальные файлы в задаче не поддерживаются.")


def _archive(task: Path) -> bytes:
    _preflight(task)
    stream = io.BytesIO()

    def add(archive: tarfile.TarFile, path: Path, arcname: str) -> None:
        # Hard links (e.g. objects shared by a local `git clone`) are stored as
        # independent regular files: the checker accepts only files and folders.
        info = path.lstat()
        member = tarfile.TarInfo(arcname)
        member.mode = stat.S_IMODE(info.st_mode)
        member.mtime = int(info.st_mtime)
        if stat.S_ISDIR(info.st_mode):
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
            for child in sorted(path.iterdir()):
                add(archive, child, f"{arcname}/{child.name}")
        elif stat.S_ISREG(info.st_mode):
            member.size = info.st_size
            with path.open("rb") as source:
                archive.addfile(member, source)
        else:
            raise CliError("Ссылки и специальные файлы в задаче не поддерживаются.")

    with tarfile.open(fileobj=stream, mode="w") as archive:
        add(archive, task, task.name)
    payload = stream.getvalue()
    if len(payload) > MAX_ARCHIVE:
        raise CliError("Архив задачи превышает лимит 16 МБ.")
    return payload


def _evaluate(payload: bytes, number: int, seed: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="hw2-check-") as temporary:
        destination = Path(temporary)
        extract_submission(payload, destination)
        passed, _ = check(destination / DIRECTORIES[number], seed, number)
        return passed


def _package_and_check(root: Path, number: int, seed: str) -> tuple[bytes, bool]:
    payload = _archive(_task_directory(root, number))
    try:
        return payload, _evaluate(payload, number, seed)
    except Exception:
        return payload, False


def check_tasks(root: Path, selection: str) -> bool:
    seed = _seed(root)
    numbers = [_task_number(selection)] if selection != "all" else [item["id"] for item in CATALOG]
    all_passed = True
    for number in numbers:
        try:
            _, passed = _package_and_check(root, number, seed)
        except (CliError, OSError, tarfile.TarError):
            passed = False
        print(f"{ID_TO_NUMBER[number]:02d}: {'зачтено' if passed else 'пока не зачтено'}")
        all_passed &= passed
    return all_passed


def submit(root: Path, selection: str, force: bool = False) -> bool:
    number = _task_number(selection)
    seed = _seed(root)
    payload, passed = _package_and_check(root, number, seed)
    submissions = root / "submissions"
    if submissions.is_symlink():
        raise CliError("Папка submissions не должна быть ссылкой.")
    submissions.mkdir(exist_ok=True)
    destination = submissions / f"{DIRECTORIES[number]}.tar"
    if destination.is_symlink():
        raise CliError("Архив отправки не должен быть ссылкой.")
    if not passed and destination.exists() and not force:
        raise CliError("Проверка пока не пройдена; прежний архив сохранён. Для замены используйте submit N --force.")
    descriptor, name = tempfile.mkstemp(prefix=".submission-", dir=submissions)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print(f"{ID_TO_NUMBER[number]:02d}: {'зачтено' if passed else 'пока не зачтено'}; архив: submissions/{destination.name}")
    return passed


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description="Локальная проверка задач по Git")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="создать учебные задачи")
    verify = commands.add_parser("check", help="проверить задачу или все задачи")
    verify.add_argument("task", metavar="N|all")
    delivery = commands.add_parser("submit", help="создать архив для сдачи")
    delivery.add_argument("task", metavar="N")
    delivery.add_argument("--force", action="store_true", help="заменить архив, даже если проверка не пройдена")
    reset = commands.add_parser(
        "revert",
        help="вернуть одну задачу к исходному состоянию",
        description="Вернуть одну задачу к исходному состоянию. Её текущие файлы, настройки и история удаляются без резервной копии; остальные задачи и архивы submissions/ не меняются.",
    )
    reset.add_argument("task", metavar="N")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "init":
            init(root)
            return 0
        if arguments.command == "check":
            return 0 if check_tasks(root, arguments.task) else 1
        if arguments.command == "submit":
            submit(root, arguments.task, force=arguments.force)
            return 0
        if arguments.command == "revert":
            revert(root, arguments.task)
            return 0
    except (CliError, OSError, tarfile.TarError) as error:
        print(f"Ошибка: {error}")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
