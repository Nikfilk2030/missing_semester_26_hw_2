from __future__ import annotations

import hashlib
import io
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile


CATALOG_PATH = Path(__file__).with_name("catalog.json")
CATALOG = json.loads(CATALOG_PATH.read_text())
DIRECTORIES = {task["id"]: task["path"] for task in CATALOG}
MAX_ARCHIVE = 16 * 1024 * 1024
GITHUB_REMOTES = (
    re.compile(r"https://github\.com/([^/?#]+/[^/?#]+)", re.ASCII | re.IGNORECASE),
    re.compile(r"git@github\.com:([^/?#]+/[^/?#]+)", re.ASCII | re.IGNORECASE),
    re.compile(r"ssh://git@github\.com/([^/?#]+/[^/?#]+)", re.ASCII | re.IGNORECASE),
)


def _validated_slug(slug: str) -> str:
    if not isinstance(slug, str):
        raise ValueError("Нужен адрес репозитория GitHub.")
    parts = slug.split("/")
    if len(parts) != 2 or slug != slug.lower():
        raise ValueError("Нужен адрес репозитория GitHub.")
    owner, repository = parts
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", owner):
        raise ValueError("Нужен адрес репозитория GitHub.")
    if not re.fullmatch(r"[a-z0-9_.-]{1,100}", repository) or repository in (".", ".."):
        raise ValueError("Нужен адрес репозитория GitHub.")
    return slug


def repository_slug(remote: str) -> str:
    if not isinstance(remote, str):
        raise ValueError("Нужен адрес репозитория GitHub.")
    for pattern in GITHUB_REMOTES:
        match = pattern.fullmatch(remote)
        if match is not None:
            slug = match.group(1)
            if slug.lower().endswith(".git"):
                slug = slug[:-4]
            return _validated_slug(slug.lower())
    raise ValueError("Нужен адрес репозитория GitHub.")


def seed_for_repository(slug: str) -> str:
    canonical = _validated_slug(slug)
    return hashlib.sha256(b"missing-semester-26-hw-2-seed-v1\0" + canonical.encode("ascii")).hexdigest()[:32]


def git(repo: Path, *args: str, strip: bool = True, allow_status1: bool = False) -> str:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": "/nonexistent", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "Student", "GIT_AUTHOR_EMAIL": "student@example.invalid", "GIT_COMMITTER_NAME": "Student", "GIT_COMMITTER_EMAIL": "student@example.invalid", "GIT_AUTHOR_DATE": "2026-01-01T12:00:00+0000", "GIT_COMMITTER_DATE": "2026-01-01T12:00:00+0000"}
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=always", "-c", "protocol.ext.allow=never", *args], cwd=repo, env=env, capture_output=True, timeout=4)
    if result.returncode not in ((0, 1) if allow_status1 else (0,)) or len(result.stdout) > 1024 * 1024:
        raise ValueError("Репозиторий повреждён или операция не завершена.")
    text = result.stdout.decode("utf-8", errors="strict")
    return text.strip() if strip else text


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def build_legacy(root: Path, seed: str, only: int | None = None) -> dict:
    token = hashlib.sha256(seed.encode()).hexdigest()[:8]
    manifests = {}
    for number, directory in DIRECTORIES.items():
        if number > 5:
            continue
        if only is not None and number != only:
            continue
        task = root / directory
        task.mkdir(parents=True)
        repo = task / "repo" if number == 5 else task
        repo.mkdir(exist_ok=True)
        git(repo, "init", "-q", "-b", "main")
        (repo / "project.txt").write_text(f"Project {token}\n")
        if number == 1:
            (repo / "note.txt").write_text("Draft\n")
        if number == 2:
            (repo / "settings.ini").write_text(f"endpoint=old-{token}\ntimeout=10\n")
        if number in (3, 5):
            (repo / "settings.json").write_text('{"retries": 1, "timeout": 10}\n')
        base = commit(repo, f"Start {token}")
        m = {"base": base, "token": token, "project_entry": git(repo, "ls-tree", "HEAD", "project.txt")}
        if number == 1:
            m["note_entry"] = git(repo, "ls-tree", "HEAD", "note.txt")
            (repo / "project.txt").write_text(f"Project {token}\nReady\n")
            (repo / "note.txt").write_text("Draft\nUnfinished\n")
        elif number == 2:
            (repo / "settings.ini").write_text(f"endpoint=new-{token}\ntimeout=30\n")
        elif number == 3:
            git(repo, "switch", "-qc", "feature")
            (repo / "settings.json").write_text('{"retries": 4, "timeout": 10}\n')
            m["feature"] = commit(repo, "Retry transient failures")
            git(repo, "switch", "-q", "main")
            (repo / "settings.json").write_text('{"retries": 1, "timeout": 30}\n')
            m["main"] = commit(repo, "Allow slow responses")
        elif number == 4:
            (repo / "project.txt").write_text(f"Project {token}\nRecovered work\n")
            m["lost"] = commit(repo, "Implement requested feature")
            git(repo, "reset", "--hard", base)
        elif number == 5:
            remote = task / "remote.git"
            git(task, "clone", "-q", "--no-hardlinks", "--bare", "repo", "remote.git")
            git(repo, "remote", "add", "origin", "../remote.git")
            (repo / "settings.json").write_text('{"retries": 4, "timeout": 10}\n')
            m["own"] = commit(repo, "Retry transient failures")
            git(task, "clone", "-q", "--no-hardlinks", "remote.git", "colleague")
            colleague = task / "colleague"
            (colleague / "team.txt").write_text(f"Team update {token}\n")
            m["colleague"] = commit(colleague, "Update team notes")
            git(colleague, "push", "-q", "origin", "main")
            shutil.rmtree(colleague)
            git(remote, "config", "receive.denyNonFastForwards", "true")
        if number == 5:
            (task / "README.md").write_text("Работайте в repo/. remote.git — локальный учебный remote.\n")
        manifests[number] = m
    return manifests


def fixture(seed: str, only: int | None = None) -> bytes:
    with tempfile.TemporaryDirectory(prefix="arena-fixture-") as temp:
        root = Path(temp)
        build(root, seed, only)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            def owner(info):
                info.uid = info.gid = 1000
                info.uname = info.gname = "student"
                return info
            for item in root.iterdir():
                archive.add(item, arcname=item.name, filter=owner)
        return stream.getvalue()


def extract_submission(payload: bytes, target: Path) -> None:
    if len(payload) > MAX_ARCHIVE:
        raise ValueError("Репозиторий превышает лимит 16 МБ.")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        total = 0
        for index, member in enumerate(archive):
            path = Path(member.name)
            total += member.size
            if index > 4096 or total > MAX_ARCHIVE or path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("Неподдерживаемая структура репозитория.")
            destination = target / path
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source:
                    destination.write_bytes(source.read())
                destination.chmod(0o644 | (member.mode & 0o111))


def clean_repository(repo: Path, bare: bool = False) -> None:
    metadata = repo if bare else repo / ".git"
    if not metadata.is_dir():
        raise ValueError("Не найден Git-репозиторий.")
    for name in ("objects/info/alternates", "objects/info/http-alternates", "info/grafts", "shallow", "commondir"):
        if (metadata / name).exists():
            raise ValueError("Внешние объекты и подмена истории не поддерживаются.")
    for name in ("config", "config.worktree", "packed-refs.lock", "index.lock"):
        path = metadata / name
        if name.endswith(".lock") and path.exists():
            raise ValueError("Завершите Git-операцию перед сдачей.")
        if path.is_file():
            path.unlink()
    (metadata / "config").write_text(f"[core]\nrepositoryformatversion = 0\nbare = {'true' if bare else 'false'}\n")


def require_regular_tree(root: Path) -> None:
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("Папка задачи недоступна.")
    count = 0
    total = 0

    def fail(error: OSError) -> None:
        raise error

    for directory, folders, files in os.walk(root, followlinks=False, onerror=fail):
        for name in (*folders, *files):
            path = Path(directory) / name
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError("Ссылки и специальные файлы не поддерживаются.")
            count += 1
            total += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if count > 4096 or total > MAX_ARCHIVE:
                raise ValueError("Репозиторий превышает лимит 16 МБ.")


def check(task: Path, seed: str, number: int) -> tuple[bool, str]:
    if number not in DIRECTORIES:
        return False, "Неизвестная задача."
    try:
        require_regular_tree(task)
        with tempfile.TemporaryDirectory(prefix="git-check-") as temp:
            copied = Path(temp) / DIRECTORIES[number]
            shutil.copytree(task, copied, symlinks=True)
            require_regular_tree(copied)
            return _check_copy(copied, seed, number)
    except (ValueError, OSError, subprocess.SubprocessError, UnicodeError, KeyError, TypeError):
        return False, "Не удалось прочитать требуемое состояние. Проверьте файлы и репозиторий."


def _check_copy(task: Path, seed: str, number: int) -> tuple[bool, str]:
    if number > 5:
        return check_extended(task, seed, number)
    with tempfile.TemporaryDirectory(prefix="expected-") as temp:
        m = build(Path(temp), seed, number)[number]
    repo = task / "repo" if number == 5 else task
    clean_repository(repo)
    if number in (1, 2, 3) and git(repo, "symbolic-ref", "--short", "HEAD") != "main":
        return False, "Переключитесь в основную ветку main."
    if number in (1, 2):
        if git(repo, "rev-list", "--count", f"{m['base']}..HEAD") != "1" or git(repo, "rev-parse", "HEAD^") != m["base"]:
            return False, "Нужен один новый коммит поверх исходной истории."
        expected = {"project.txt": f"Project {m['token']}\n", "note.txt": "Draft\n"} if number == 1 else {"project.txt": f"Project {m['token']}\n", "settings.ini": f"endpoint=new-{m['token']}\ntimeout=10\n"}
        if number == 1:
            expected["project.txt"] += "Ready\n"
        if set(git(repo, "ls-tree", "--name-only", "HEAD").splitlines()) != set(expected):
            return False, "В коммит попали лишние файлы или потеряны исходные."
        if any(git(repo, "show", f"HEAD:{name}", strip=False) != content for name, content in expected.items()):
            return False, "Содержимое коммита не соответствует условию."
        preserved = "note" if number == 1 else "project"
        if git(repo, "ls-tree", "HEAD", f"{preserved}.txt") != m[f"{preserved}_entry"]:
            return False, "Другие файлы в коммите должны остаться неизменными."
        pending = (repo / ("note.txt" if number == 1 else "settings.ini")).read_text()
        wanted = "Draft\nUnfinished\n" if number == 1 else f"endpoint=new-{m['token']}\ntimeout=30\n"
        if pending != wanted:
            return False, "Незавершённое изменение рабочего файла потеряно."
    elif number == 3:
        if git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] != [m["main"], m["feature"]]:
            return False, "Нужен merge-коммит main с сохранением обеих исходных линий истории."
        if json.loads(git(repo, "show", "HEAD:settings.json")) != {"retries": 4, "timeout": 30}:
            return False, "Итоговая конфигурация должна сохранить оба изменения."
        if git(repo, "ls-files", "--unmerged"):
            return False, "В индексе остался конфликт."
        if git(repo, "rev-parse", "refs/heads/feature") != m["feature"] or set(git(repo, "ls-tree", "--name-only", "HEAD").splitlines()) != {"project.txt", "settings.json"} or git(repo, "ls-tree", "HEAD", "project.txt") != m["project_entry"]:
            return False, "Потеряны исходные файлы или изменена ветка feature."
    elif number == 4:
        if git(repo, "rev-parse", "refs/heads/recovered") != m["lost"] or git(repo, "rev-parse", "refs/heads/main") != m["base"]:
            return False, "Ветка recovered должна вернуть исходный потерянный коммит, main — остаться прежней."
    elif number == 5:
        remote = task / "remote.git"
        clean_repository(remote, bare=True)
        git(remote, "merge-base", "--is-ancestor", m["colleague"], "main")
        if json.loads(git(remote, "show", "main:settings.json")) != {"retries": 4, "timeout": 10} or git(remote, "show", "main:team.txt") != f"Team update {m['token']}":
            return False, "В локальном remote должны сохраниться ваше изменение и обновление коллеги."
        if set(git(remote, "ls-tree", "--name-only", "main").splitlines()) != {"project.txt", "settings.json", "team.txt"} or git(remote, "ls-tree", "main", "project.txt") != m["project_entry"]:
            return False, "В remote потеряны исходные файлы или добавлены лишние."
    if number in (2, 3):
        project = repo / "project.txt"
        if not project.is_file() or project.read_bytes() != f"Project {m['token']}\n".encode() or project.stat().st_mode & 0o111:
            return False, "Восстановите неизменяемый рабочий файл project.txt."
    return True, "Зачтено."


NESTED = {6, 7, 20, 21, 22, 24, 30, 31, 32}


def put(repo: Path, name: str, content: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def files_at(repo: Path, ref: str) -> dict[str, str]:
    return {name: git(repo, "show", f"{ref}:{name}", strip=False) for name in git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()}


def working_files(repo: Path) -> dict[str, str]:
    return {str(path.relative_to(repo)): path.read_text() for path in repo.rglob("*") if path.is_file() and ".git" not in path.relative_to(repo).parts}


def build_extended(root: Path, seed: str, number: int) -> dict:
    task = root / DIRECTORIES[number]
    repo = task / "repo" if number in NESTED else task
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    token = hashlib.sha256(seed.encode()).hexdigest()[:8]
    project = f"Project {token}\n"
    put(repo, "project.txt", project)
    if number == 6:
        control = task / "control"
        control.mkdir()
        git(control, "init", "-q", "-b", "main")
        git(control, "config", "user.name", "Control")
        git(control, "config", "user.email", "control@example.invalid")
        put(control, "project.txt", project)
        return {"token": token, "control": commit(control, "Control"), "project": project}
    initial = {
        7: {"settings.ini": "timeout=10\n"},
        8: {"ready.txt": "old\n", "draft.txt": "old\n"},
        9: {"build/cache.log": f"Cache {token}\n", "app.py": "print('ready')\n"},
        10: {"mistake.txt": "original\n", "draft.txt": "old\n"},
        11: {"work.txt": "old\n"}, 12: {"work.txt": "old\n"}, 13: {"work.txt": "old\n"},
        14: {"app.txt": "old\n", "manual.txt": "old\n"},
        15: {"feature.txt": "status=planned\n"},
        18: {"service.txt": "broken\n", "work.txt": "old\n"},
        19: {"settings.ini": "unsafe=false\n"},
        21: {"work.txt": "old\n"},
        23: {"bug.txt": "broken\n"},
        30: {"behavior.txt": "good\n", "test.sh": '#!/bin/sh\n[ "$(cat behavior.txt)" = good ]\n'},
    }.get(number, {})
    for name, content in initial.items():
        put(repo, name, content)
    if number == 30:
        (repo / "test.sh").chmod(0o755)
    base = commit(repo, f"Start {token}")
    m = {"token": token, "base": base, "base_files": files_at(repo, "HEAD")}
    if number in (11, 12, 13, 14, 26, 27, 30, 32):
        git(repo, "tag", "start")
    if number in (7, 30):
        for index in range(1, 7):
            put(repo, "notes.txt", f"Revision {index} {token}\n")
            if index == 3:
                put(repo, "settings.ini" if number == 7 else "behavior.txt", "timeout=30\n" if number == 7 else "bad\n")
            revision = commit(repo, f"Update {index}")
            if index == 3:
                m["answer"] = revision
        put(task, "answer.txt", "")
    elif number == 8:
        put(repo, "ready.txt", "ready\n")
        put(repo, "draft.txt", "draft\n")
        git(repo, "add", ".")
    elif number == 10:
        put(repo, "mistake.txt", "accidental\n")
        put(repo, "draft.txt", "draft\n")
        put(repo, "notes.txt", f"Keep {token}\n")
    elif number in (11, 12, 13):
        put(repo, "work.txt", f"new {token}\n")
        m["latest"] = commit(repo, "Experiment")
        if number == 13:
            put(repo, "work.txt", "unfinished\n")
            put(repo, "notes.txt", f"Keep {token}\n")
    elif number == 14:
        put(repo, "app.txt", "ready\n")
        commit(repo, "Application")
        put(repo, "manual.txt", "ready\n")
    elif number in (16, 17, 25):
        git(repo, "switch", "-qc", "feature")
        put(repo, "feature.txt", f"Feature {token}\n")
        m["feature"] = commit(repo, "Feature")
        git(repo, "switch", "-q", "main")
        if number != 16:
            put(repo, "main.txt", f"Main {token}\n")
            m["main"] = commit(repo, "Main update")
        if number == 25:
            git(repo, "switch", "-q", "feature")
    elif number == 18:
        git(repo, "switch", "-qc", "feature")
        put(repo, "feature.txt", f"Feature {token}\n")
        m["feature"] = commit(repo, "Feature foundation")
        put(repo, "work.txt", "stage\n")
        git(repo, "add", "work.txt")
        put(repo, "work.txt", "draft\n")
    elif number == 19:
        put(repo, "settings.ini", "unsafe=true\n")
        m["bad"] = commit(repo, "Unsafe default")
        put(repo, "useful.txt", f"Useful {token}\n")
        commit(repo, "Useful addition")
    elif number == 23:
        git(repo, "switch", "-qc", "feature")
        put(repo, "bug.txt", "fixed\n")
        m["fix"] = commit(repo, "Fix bug")
        put(repo, "experiment.txt", "unfinished\n")
        m["feature"] = commit(repo, "Experiment")
        git(repo, "switch", "-q", "main")
    elif number == 26:
        put(repo, "feature.txt", "status=planned\n")
        commit(repo, "Draft feature")
        put(repo, "docs.txt", f"Documentation {token}\n")
        commit(repo, "Draft docs")
        put(repo, "feature.txt", "status=ready\n")
        commit(repo, "Finish feature")
    elif number in (27, 28):
        if number == 28:
            git(repo, "checkout", "-q", "--detach")
        put(repo, "feature.txt", f"Feature {token}\n")
        m["work_commit"] = commit(repo, "Feature work")
    elif number == 29:
        git(repo, "switch", "-qc", "feature")
        put(repo, "feature.txt", "enabled\n")
        put(repo, "bug.txt", "broken\n")
        commit(repo, "Feature with bug")
        git(repo, "switch", "-q", "main")
        git(repo, "merge", "--no-ff", "-m", "Publish feature", "feature")
        git(repo, "revert", "--no-edit", "-m", "1", "HEAD")
        put(repo, "useful.txt", f"Useful {token}\n")
        m["main"] = commit(repo, "Useful addition")
        git(repo, "switch", "-q", "feature")
        put(repo, "bug.txt", "fixed\n")
        m["feature"] = commit(repo, "Fix feature")
        git(repo, "switch", "-q", "main")
    elif number == 31:
        put(repo, "project.txt", project + "Release\n")
        commit(repo, "Release")
        git(repo, "tag", "release")
        m["answers"] = {"tree": git(repo, "rev-parse", "HEAD^{tree}"), "blob": git(repo, "rev-parse", "HEAD:project.txt"), "previous_blob": git(repo, "rev-parse", "HEAD^:project.txt")}
        put(task, "answer.json", '{"tree":"","blob":"","previous_blob":""}\n')
    if number in (20, 21, 22, 24, 32):
        git(task, "clone", "-q", "--no-hardlinks", "--bare", "repo", "remote.git")
        git(repo, "remote", "add", "origin", "../remote.git")
        git(repo, "fetch", "-q", "origin")
        m["remote"] = base
        if number in (22, 32):
            if number == 22:
                git(repo, "switch", "-qc", "feature")
            put(repo, "feature.txt", f"Feature {token}\n")
            m["feature"] = commit(repo, "Feature work")
        if number in (21, 32):
            git(task, "clone", "-q", "--no-hardlinks", "remote.git", "colleague")
            colleague = task / "colleague"
            put(colleague, "team.txt", f"Team {token}\n")
            m["remote"] = commit(colleague, "Team update")
            git(colleague, "push", "-q", "origin", "main")
            shutil.rmtree(colleague)
        if number == 21:
            put(repo, "work.txt", "stage\n")
            git(repo, "add", "work.txt")
            put(repo, "work.txt", "draft\n")
    m["head"] = git(repo, "rev-parse", "HEAD")
    m["files"] = files_at(repo, "HEAD")
    m["index"] = git(repo, "write-tree")
    m["work"] = working_files(repo)
    if number == 20:
        shutil.rmtree(repo)
    return m


def build(root: Path, seed: str, only: int | None = None) -> dict:
    manifests = build_legacy(root, seed, only)
    for number in DIRECTORIES:
        if number > 5 and (only is None or number == only):
            manifests[number] = build_extended(root, seed, number)
    return manifests


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def ref_value(repo: Path, name: str) -> str:
    value = git(repo, "rev-parse", "--verify", name)
    git(repo, "cat-file", "-e", value)
    return value


def require_branch(repo: Path, branch: str) -> None:
    require(git(repo, "symbolic-ref", "HEAD") == f"refs/heads/{branch}", f"HEAD должен указывать на ветку {branch}.")


def require_files(repo: Path, ref: str, expected: dict[str, str]) -> None:
    require(files_at(repo, ref) == expected, "Состав или содержимое файлов коммита не соответствует условию.")
    for entry in git(repo, "ls-tree", "-r", ref).splitlines():
        mode, rest = entry.split(" ", 1)
        name = rest.split("\t", 1)[1]
        require(mode == ("100755" if name == "test.sh" else "100644"), "Изменены права или тип исходного файла.")


def require_work(repo: Path, expected: dict[str, str]) -> None:
    for name, text in expected.items():
        path = repo / name
        require(path.is_file() and path.read_text() == text, f"Рабочий файл {name} не соответствует условию.")
        require(bool(path.stat().st_mode & 0o111) == (name == "test.sh"), f"Изменены права рабочего файла {name}.")


def require_clean(repo: Path, expected: dict[str, str]) -> None:
    require(git(repo, "write-tree") == ref_value(repo, "HEAD^{tree}"), "Индекс должен соответствовать итоговому коммиту.")
    require_work(repo, expected)


def require_commit(repo: Path, parent: str) -> None:
    require(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [parent], "Нужен один обычный коммит поверх заданной основы.")


def local_config(repo: Path, key: str) -> str:
    config = repo / ".git" / "config"
    if not config.is_file() or config.stat().st_size > 65536:
        raise ValueError("Локальная конфигурация отсутствует или слишком велика.")
    with tempfile.TemporaryDirectory(prefix="config-read-") as temp:
        return git(Path(temp), "config", "--file", str(config.resolve()), "--no-includes", "--get", key, allow_status1=True)


def answer_object(repo: Path, answer: str, expected: str, kind: str) -> None:
    require(bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", answer)), "Нужен полный SHA или однозначное сокращение от 7 символов.")
    require(expected.startswith(answer.lower()), "Найден другой объект истории. Проверьте требование задачи.")
    resolved = ref_value(repo, answer.lower())
    require(git(repo, "cat-file", "-t", resolved) == kind and resolved == expected, "Найден другой объект истории. Проверьте требование задачи.")


def require_ignore_rules(patterns: str) -> None:
    with tempfile.TemporaryDirectory(prefix="ignore-rules-") as temp:
        repo = Path(temp)
        git(repo, "init", "-q")
        put(repo, ".gitignore", patterns)
        for path in ("build/cache.log", "new.log", "deep/nested/new.log"):
            require(bool(git(repo, "check-ignore", "--no-index", "--", path)), "Правила должны исключать .log на любой глубине.")
        for path in ("project.txt", "app.py", "new.py", "nested/new.txt"):
            output = git(repo, "check-ignore", "--no-index", "--non-matching", "--verbose", "--", path, allow_status1=True)
            fields = output.split("\t")[0].split(":", 2)
            require(len(fields) == 3 and (not fields[2] or fields[2].startswith("!")), "Правила не должны игнорировать исходники .py и .txt.")


def clone_origin_is_sibling(origin: str, task: Path) -> bool:
    source = PurePosixPath(origin)
    if source.is_absolute():
        return ".." not in source.parts and source.parts[-2:] == (task.name, "remote.git")
    return source.parts == ("..", "remote.git")


def check_extended(task: Path, seed: str, number: int) -> tuple[bool, str]:
    try:
        with tempfile.TemporaryDirectory(prefix="expected-") as temp:
            m = build_extended(Path(temp), seed, number)
        repo = task / "repo" if number in NESTED else task
        config = {}
        if number == 6:
            for key in ("user.name", "user.email"):
                config[key] = local_config(repo, key)
            require(config == {"user.name": "Student", "user.email": "student@example.invalid"}, "В repo/.git/config нужны локальные user.name = Student и user.email = student@example.invalid. Глобальные настройки (--global) их не заменяют. Если коммит уже создан с нужным автором, переделывать его не нужно.")
            control = task / "control"
            require(local_config(control, "user.name") == "Control" and local_config(control, "user.email") == "control@example.invalid", "Настройки соседнего control изменены.")
            clean_repository(control)
            require(ref_value(control, "refs/heads/main") == m["control"], "История control изменена.")
            require_branch(control, "main")
            require_files(control, "HEAD", {"project.txt": m["project"]})
            require_clean(control, {"project.txt": m["project"]})
        if number == 20:
            origin = local_config(repo, "remote.origin.url")
            require(clone_origin_is_sibling(origin, task), "Origin должен указывать на соседний локальный remote.git.")
        clean_repository(repo)
        remote = task / "remote.git"
        if number in (20, 21, 22, 24, 32):
            clean_repository(remote, bare=True)
            git(remote, "rev-list", "--objects", "--missing=error", "--all")
            require(ref_value(remote, "refs/heads/main") == m["remote"], "Main локального remote не должна изменяться.")
        if number == 6:
            require_branch(repo, "main")
            require(len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 1, "Нужен один корневой коммит.")
            require(git(repo, "show", "-s", "--format=%an%n%ae%n%cn%n%ce", "HEAD") == "Student\nstudent@example.invalid\nStudent\nstudent@example.invalid", "Автор и коммитер должны соответствовать локальным настройкам.")
            require_files(repo, "HEAD", {"project.txt": m["project"]})
            require_clean(repo, {"project.txt": m["project"]})
        elif number in (7, 30, 31):
            require_branch(repo, "main")
            require(ref_value(repo, "HEAD") == m["head"], "Верните исходную main перед сдачей.")
            require_clean(repo, m["files"])
            if number == 31:
                require(ref_value(repo, "refs/tags/release") == m["head"], "Тег release изменён.")
                answers = json.loads((task / "answer.json").read_text())
                require(isinstance(answers, dict) and set(answers) == set(m["answers"]), "Заполните три именованных поля answer.json.")
                for key, value in m["answers"].items():
                    require(isinstance(answers[key], str), "Ответы должны быть строками SHA.")
                    answer_object(repo, answers[key].strip(), value, "tree" if key == "tree" else "blob")
            else:
                answer_object(repo, (task / "answer.txt").read_text().strip(), m["answer"], "commit")
        elif number == 8:
            require_branch(repo, "main")
            require(ref_value(repo, "HEAD") == m["base"], "Main не должна изменяться.")
            require_files(repo, git(repo, "write-tree"), {**m["base_files"], "ready.txt": "ready\n"})
            require_work(repo, m["work"])
        elif number == 9:
            require_branch(repo, "main")
            require_commit(repo, m["base"])
            files = files_at(repo, "HEAD")
            require(set(files) == {"project.txt", "app.py", ".gitignore"}, "В коммите должны остаться исходники и .gitignore, без артефакта.")
            expected = {key: value for key, value in m["base_files"].items() if key != "build/cache.log"}
            require_files(repo, "HEAD", {**expected, ".gitignore": files[".gitignore"]})
            require_clean(repo, {**expected, ".gitignore": files[".gitignore"], "build/cache.log": m["base_files"]["build/cache.log"]})
            require_ignore_rules(files[".gitignore"])
        elif number == 10:
            require_branch(repo, "main")
            require(ref_value(repo, "HEAD") == m["base"] and git(repo, "write-tree") == m["index"], "Main и индекс должны остаться прежними.")
            require_work(repo, {**m["work"], "mistake.txt": "original\n"})
        elif number in (11, 12, 13):
            require_branch(repo, "main")
            require(ref_value(repo, "HEAD") == m["base"], "Main должна указывать на исходный start.")
            expected_index = m["index"] if number == 11 else ref_value(repo, "HEAD^{tree}")
            require(git(repo, "write-tree") == expected_index, "Индекс не соответствует требуемому состоянию.")
            expected_work = m["work"] if number != 13 else {**m["base_files"], "notes.txt": m["work"]["notes.txt"]}
            require_work(repo, expected_work)
        elif number == 14:
            require_branch(repo, "main")
            require_commit(repo, m["base"])
            require_files(repo, "HEAD", m["work"])
            require_clean(repo, m["work"])
        elif number == 15:
            require_branch(repo, "feature")
            require(ref_value(repo, "refs/heads/main") == m["base"], "Main изменена.")
            require_commit(repo, m["base"])
            expected = {**m["base_files"], "feature.txt": "status=ready\n"}
            require_files(repo, "HEAD", expected)
            require_clean(repo, expected)
        elif number in (16, 17, 25):
            expected = {**m["base_files"], "feature.txt": f"Feature {m['token']}\n"}
            if number != 16:
                expected["main.txt"] = f"Main {m['token']}\n"
            if number == 25:
                require_branch(repo, "feature")
                require(ref_value(repo, "refs/heads/main") == m["main"], "Main изменена.")
                require_commit(repo, m["main"])
            else:
                require_branch(repo, "main")
                require(ref_value(repo, "refs/heads/feature") == m["feature"], "Feature изменена.")
                if number == 16:
                    require(ref_value(repo, "HEAD") == m["feature"], "Main должна указывать точно на исходный коммит feature.")
                else:
                    require(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [m["main"], m["feature"]], "Нужен merge-коммит с исходными main и feature как родителями.")
            require_files(repo, "HEAD", expected)
            require_clean(repo, expected)
        elif number == 18:
            require_branch(repo, "feature")
            require(ref_value(repo, "HEAD") == m["feature"] and git(repo, "write-tree") == m["index"], "Восстановите исходные feature и её индекс.")
            require_work(repo, m["work"])
            require(git(repo, "rev-list", "--parents", "-n", "1", "refs/heads/main").split()[1:] == [m["base"]], "В main нужен один исправляющий коммит.")
            require_files(repo, "refs/heads/main", {**m["base_files"], "service.txt": "fixed\n"})
        elif number in (19, 29):
            require_branch(repo, "main")
            git(repo, "merge-base", "--is-ancestor", m["head"], "HEAD")
            if number == 19:
                expected = {**m["files"], "settings.ini": "unsafe=false\n"}
            else:
                require(ref_value(repo, "refs/heads/feature") == m["feature"], "Feature изменена.")
                expected = {**m["files"], "feature.txt": "enabled\n", "bug.txt": "fixed\n"}
            require_files(repo, "HEAD", expected)
            require_clean(repo, expected)
        elif number in (20, 21, 22, 24):
            require(ref_value(repo, "refs/heads/main") == m["base"], "Локальная main изменена.")
            require(ref_value(repo, "HEAD") == m["head"], "Текущий коммит изменён.")
            if number != 22:
                require_branch(repo, "main")
            require(git(repo, "write-tree") == m["index"], "Локальный индекс изменён.")
            require_work(repo, m["work"])
            if number == 21:
                require(ref_value(repo, "refs/remotes/origin/main") == m["remote"], "Origin/main не содержит обновления коллеги.")
                git(repo, "rev-list", "--objects", "--missing=error", "refs/remotes/origin/main")
            elif number == 22:
                require(ref_value(repo, "refs/heads/feature") == m["feature"] and ref_value(remote, "refs/heads/feature") == m["feature"], "Feature в обоих репозиториях должна указывать на исходный коммит.")
            elif number == 24:
                tag = ref_value(repo, "refs/tags/release-1")
                require(git(repo, "cat-file", "-t", tag) == "tag", "Нужен аннотированный тег.")
                require(ref_value(repo, "refs/tags/release-1^{commit}") == m["base"], "Тег указывает не на заданный выпуск.")
                require(bool(git(repo, "cat-file", "tag", tag).partition("\n\n")[2].strip()), "У тега должно быть непустое сообщение.")
                require(ref_value(remote, "refs/tags/release-1") == tag, "В remote отсутствует тот же объект тега.")
        elif number == 23:
            require_branch(repo, "main")
            require_commit(repo, m["base"])
            require(ref_value(repo, "refs/heads/feature") == m["feature"], "Feature изменена.")
            expected = {**m["base_files"], "bug.txt": "fixed\n"}
            require_files(repo, "HEAD", expected)
            require_clean(repo, expected)
        elif number == 26:
            require_branch(repo, "main")
            parent = ref_value(repo, "HEAD^")
            require(git(repo, "rev-list", "--parents", "-n", "1", parent).split()[1:] == [m["base"]], "Первый логический коммит должен продолжать start.")
            require_commit(repo, parent)
            require_files(repo, parent, {**m["base_files"], "feature.txt": "status=ready\n"})
            require_files(repo, "HEAD", m["files"])
            require_clean(repo, m["files"])
        elif number in (27, 28):
            require_branch(repo, "feature" if number == 27 else "rescue")
            require(ref_value(repo, "HEAD") == m["work_commit"] and ref_value(repo, "refs/heads/main") == m["base"], "Нужны исходные коммиты на требуемых ветках.")
            require_clean(repo, m["files"])
        elif number == 32:
            require_branch(repo, "integration")
            require(ref_value(repo, "refs/heads/feature") == m["feature"], "Сохраните исходный коммит в feature.")
            require(ref_value(repo, "refs/heads/main") == m["remote"], "Локальная main должна совпадать с исходной main коллеги.")
            for ancestor in (m["remote"], m["feature"]):
                git(repo, "merge-base", "--is-ancestor", ancestor, "HEAD")
            require(ref_value(remote, "refs/heads/integration") == ref_value(repo, "HEAD"), "Опубликуйте тот же коммит integration в remote.")
            expected = {**m["base_files"], "feature.txt": f"Feature {m['token']}\n", "team.txt": f"Team {m['token']}\n"}
            require_files(repo, "HEAD", expected)
            require_clean(repo, expected)
            require_files(remote, "refs/heads/integration", expected)
        if number in (11, 12, 13, 14, 26, 27, 30, 32):
            require(ref_value(repo, "refs/tags/start") == m["base"], "Тег start должен остаться прежним.")
        return True, "Зачтено."
    except (ValueError, OSError, subprocess.SubprocessError, UnicodeError, KeyError, TypeError) as error:
        return False, str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else "Не удалось прочитать требуемое состояние. Проверьте файлы и репозиторий."
