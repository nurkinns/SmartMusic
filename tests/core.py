"""
core.py — мини-фреймворк для автотестов SmartMusic.

Ничего не трогает в проекте: только собирает тесты, запускает их и
печатает результат. Цвета (зелёный/красный) и живой процент выполнения
выводятся в консоль.

Как подключить новый модуль тестов:
    1. Положи файл tests/test_my_area.py.
    2. Внутри сделай S = suite("Название раздела") и декорируй функции
       через @S.add("человекочитаемое название проверки").
    3. Добавь модуль в список MODULES в tests/runner.py.
"""

import sys
import time
import traceback
from pathlib import Path

# Корень проекта — родитель этой папки. Нужен, чтобы тесты видели src/,
# config и т.д. независимо от того, откуда запущен скрипт.
ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Переключаем консоль на UTF-8 (как это делает config.py), чтобы эмодзи и
# кириллица не падали на русской Windows.
for _stream in ("stdout", "stderr"):
    _obj = getattr(sys, _stream, None)
    if _obj is not None and hasattr(_obj, "reconfigure"):
        try:
            _obj.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _enable_ansi() -> None:
    """Включает ANSI-цвета в консоли Windows (Windows Terminal, conhost 10+)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 0x0004)
    except Exception:
        # Если не получилось — просто печатаем без цветов, тесты не страдают.
        pass


_enable_ansi()

# ANSI-коды
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

_COLOR = sys.stdout.isatty() or sys.platform != "win32" or bool(
    getattr(sys, "__stdout__", None) and sys.__stdout__.isatty())


def paint(code: str, text: str) -> str:
    if not _COLOR:
        return text
    return f"{code}{text}{RESET}"


class Skip(Exception):
    """Тест осознанно пропущен (например, демон Ollama не запущен)."""


class _Suite:
    """Раздел тестов: заголовок + список (имя, функция)."""

    def __init__(self, title: str):
        self.title = title
        self.cases = []

    def add(self, name: str):
        def decorator(fn):
            self.cases.append((name, fn))
            return fn

        return decorator


def suite(title: str) -> _Suite:
    return _Suite(title)