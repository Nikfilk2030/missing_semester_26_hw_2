# Домашнее задание 2: Git

В репозитории — [32 задачи](TASKS.md) и локальная проверка. Нужны Linux (для Windows — Ubuntu в WSL), Git 2.28+ и Python 3.10+.

## Начало работы

1. Нажмите **Use this template → Create a new repository** и создайте **публичный репозиторий в своём аккаунте GitHub**.
2. Откройте терминал Linux или Ubuntu в WSL и клонируйте **свой** репозиторий. Замените `USERNAME` и `REPO` на свой логин и название репозитория:

   ```sh
   cd ~
   git clone https://github.com/USERNAME/REPO.git
   cd REPO
   python3 hw2.py init
   ```

В WSL работайте в домашнем каталоге Linux (`~/`), не в `/mnt/c/`. Не переименовывайте свой репозиторий и не меняйте его `origin`: к адресу привязана сдача.

## Решение и сдача

Читайте [условия задач](TASKS.md) и работайте в соответствующих папках `workspace/`. Например, задача 1 находится в `workspace/01_setup/`. Это отдельные учебные Git-репозитории; команды сдачи ниже выполняйте из корня своего репозитория.

```sh
python3 hw2.py check 1
python3 hw2.py submit 1
git add .hw2/manifest.json submissions/
git commit -m "Submit homework"
git push
```

Вместо `1` указывайте номер задачи из `TASKS.md`. `check` показывает только результат проверки; `submit` сохраняет текущее решение для отправки. После исправлений повторите проверку и сдачу.

После `git push` заполните [форму сдачи](https://forms.gle/1kugzZBwpvExhqoQ6): укажите ссылку на **свой публичный репозиторий**, не на этот шаблон.

Если `git commit` при сдаче просит указать автора, настройте `user.name` и `user.email` в корневом репозитории.

`workspace/` не попадает в GitHub: отправляются архивы в `submissions/`. В архиве сохраняется полное состояние учебного Git-репозитория, в том числе история, незакоммиченные изменения и `.git/config`. **Не помещайте туда токены, секреты и личные файлы.**

Если переходите на другой компьютер, снова клонируйте свой репозиторий и запустите `python3 hw2.py init`: последние отправленные архивы восстановятся. Неотправленные изменения из `workspace/` останутся на старом компьютере.

<details>
<summary>Проверка преподавателем</summary>

Проверяйте зафиксированный на дедлайн коммит. Собирайте проверяющий контейнер **из доверенной копии шаблона**, а не из репозитория студента; код студента не запускайте. Адрес `OWNER/REPO` берите из присланной ссылки:

```sh
docker build -t hw2-grader -f Dockerfile.grader .
docker run --rm --network=none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --pids-limit=128 --memory=1g --cpus=1 \
  --mount type=bind,src=/ABS/STUDENT_CHECKOUT,dst=/student,readonly \
  hw2-grader /student --repository OWNER/REPO --json
```

</details>
