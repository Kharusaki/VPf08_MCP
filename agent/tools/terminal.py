"""Инструмент «Терминал» с белым списком безопасных команд.

Полноценный shell агенту не нужен и опасен. Здесь поддерживаются
только команды чтения: посмотреть папку, прочитать файл, узнать версию и т.п.
Любые команды изменения системы, удаления, запуска скриптов запрещены.
"""

from __future__ import annotations

import getpass
import logging
import platform
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List

from ..config import SETTINGS
from .file_io import list_directory, read_file

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 5_000
FORBIDDEN_CHARS = set("&|><;`\n\r")


def _split(command: str) -> List[str]:
    """Разобрать команду на слова, не давая внедрить shell-операторы."""
    if any(char in command for char in FORBIDDEN_CHARS):
        raise ValueError("В команде есть запрещённые символы (| & > < ; и т.п.).")

    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        raise ValueError(f"Не удалось разобрать команду: {exc}") from exc

    return [part.strip('"').strip("'") for part in parts]


def _run(args: List[str]) -> Dict[str, Any]:
    """Запустить безопасную программу без shell."""
    try:
        completed = subprocess.run(
            args,
            cwd=str(SETTINGS.workspace_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Команда не выполнилась: {exc}") from exc

    stdout = (completed.stdout or "")[:MAX_OUTPUT_CHARS]
    stderr = (completed.stderr or "")[:MAX_OUTPUT_CHARS]
    return {
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def run_terminal_command(command: str) -> Dict[str, Any]:
    """Выполнить безопасную read-only команду."""
    command = (command or "").strip()
    if not command:
        raise ValueError("Пустая команда.")

    parts = _split(command)
    if not parts:
        raise ValueError("Пустая команда.")
    name = parts[0].lower()
    args = parts[1:]

    # Просмотр папки
    if name in {"ls", "dir"}:
        path = args[0] if args else "."
        return {"command": command, "returncode": 0, "result": list_directory(path), "stderr": ""}

    # Текущая папка
    if name in {"pwd", "cd"}:
        return {"command": command, "returncode": 0, "stdout": str(SETTINGS.workspace_dir), "stderr": ""}

    # Чтение файла
    if name in {"cat", "type"}:
        if not args:
            raise ValueError("Укажите имя файла: cat <файл>.")
        return {"command": command, "returncode": 0, "stdout": read_file(args[0]), "stderr": ""}

    # Эхо
    if name == "echo":
        return {"command": command, "returncode": 0, "stdout": " ".join(args), "stderr": ""}

    # Поиск программы
    if name in {"where", "which"}:
        if not args:
            raise ValueError("Укажите имя программы: where <имя>.")
        found = shutil.which(args[0])
        if not found:
            raise ValueError(f"Программа «{args[0]}» не найдена.")
        return {"command": command, "returncode": 0, "stdout": found, "stderr": ""}

    # Версии Python и pip — только с флагом --version
    if name in {"python", "python3"} and args == ["--version"]:
        return {"command": command, **_run([sys.executable, "--version"])}
    if name in {"pip", "pip3"} and args == ["--version"]:
        return {"command": command, **_run([sys.executable, "-m", "pip", "--version"])}

    # git status — только просмотр состояния репозитория
    if name == "git" and args in (["status"], ["status", "--short"]):
        return {"command": command, **_run(["git", *args])}

    # Системная информация
    if name in {"date", "time"}:
        return {
            "command": command,
            "returncode": 0,
            "stdout": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "stderr": "",
        }
    if name == "whoami":
        return {"command": command, "returncode": 0, "stdout": getpass.getuser(), "stderr": ""}
    if name in {"uname", "ver"}:
        return {"command": command, "returncode": 0, "stdout": platform.platform(), "stderr": ""}

    raise ValueError(
        f"Команда «{name}» не разрешена. Доступны только безопасные команды: "
        "ls/dir, pwd, cat/type, echo, where/which, python --version, "
        "pip --version, git status, date, whoami, uname/ver."
    )
