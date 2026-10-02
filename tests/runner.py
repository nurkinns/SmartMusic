"""
runner.py — запускает все автотесты SmartMusic и печатает результат.

Формат вывода (один тест на строку):
  ✅  [12/48 · 25%] название проверки

Зелёный — тест прошёл, красный — сломался, жёлтый — пропущен
(Ollama выключена, не хватает железа и т.п.).

Использование (из корня проекта):
    python run_tests.py
"""

import sys
import importlib
import traceback
from pathlib import Path
from types import ModuleType
from typing import List

from tests.core import (
    GREEN,
    RED,
    YELLOW,
    CYAN,
    BOLD,
    DIM,
    RESET,
    paint,
    Skip,
)

# Список модулей для тестирования (порядок важен).
MODULES: List[str] = [
    "tests.test_dj_brain",
    "tests.test_damage_window",
    "tests.test_crossfade",
    "tests.test_gsi",
    "tests.test_ollama",
    "tests.test_vibe_classifier",
    "tests.test_track_filter",
    "tests.test_taste_profile",
    "tests.test_octave",
    "tests.test_audio_cache",
]


def _load_modules() -> List[ModuleType]:
    loaded = []
    for name in MODULES:
        try:
            mod = importlib.import_module(name)
            loaded.append(mod)
        except Exception as e:
            print(f"  {paint(RED, '❌ НЕ УДАЛОСЬ ЗАГРУЗИТЬ МОДУЛЬ:')} {name}")
            print(f"     {type(e).__name__}: {e}")
            traceback.print_exc()
            sys.exit(1)
    return loaded


def _collect_cases(modules: List[ModuleType]):
    cases: List[tuple] = []
    for mod in modules:
        for attr_name in dir(mod):
            if attr_name == "CASES":
                cases.extend((mod.__name__, name, fn)
                             for name, fn in getattr(mod, attr_name))
    if not cases:
        # Резерв: ищем атрибут SUITE/S и достаём из него.
        for mod in modules:
            s = getattr(mod, "S", None)
            if s:
                cases.extend(
                    (mod.__name__, name, fn) for name, fn in s.cases
                )
    if not cases:
        # Совсем последний резерв: test-функции по имени.
        for mod in modules:
            for attr_name in dir(mod):
                if attr_name.startswith("test_") and callable(
                        getattr(mod, attr_name)):
                    title = getattr(getattr(mod, attr_name), "__test_title__",
                                    attr_name)
                    cases.append(
                        (mod.__name__, title, getattr(mod, attr_name)))
    return cases


def main() -> int:
    modules = _load_modules()

    # Собираем все тесты.
    cases = _collect_cases(modules)
    total = len(cases)
    if total == 0:
        print(paint(RED, "❌ Нет ни одного теста для запуска!"))
        return 1

    passed = failed = skipped = 0
    name_width = max(len(name) for _, name, _ in cases) if cases else 40

    header = (
        f"\n{paint(CYAN + BOLD, 'SmartMusic — автотесты')}\n"
        f"{paint(DIM, '=' * 70)}\n"
        f"{len(modules)} модулей, {total} проверок\n"
        f"{paint(DIM, '=' * 70)}\n"
    )
    print(header)

    for i, (modname, name, fn) in enumerate(cases, 1):
        result = "passed"
        msg = ""
        try:
            fn()
        except Skip as e:
            result = "skipped"
            msg = str(e)
        except Exception as e:
            tback = traceback.format_exception_only(type(e), e)[-1].strip()
            msg = tback
            result = "failed"

        pct = round(i * 100 / total)
        progress = f"[{i}/{total}  {pct:>3}%]"

        if result == "passed":
            passed += 1
            sym = "✅"
            color = GREEN
        elif result == "skipped":
            skipped += 1
            sym = "➖"
            color = YELLOW
            msg = msg or "пропущен"
        else:
            failed += 1
            sym = "❌"
            color = RED

        line = f"  {sym} {name:<{name_width}} {progress}"
        print(paint(color, line))
        if msg:
            print(paint(DIM if result == "skipped" else RED, f"       {msg[:120]}"))

    # Сводка.
    summary = (
        f"\n{paint(CYAN + BOLD, 'Результат')}\n"
        f"  {paint(GREEN, f'✅ пройдено: {passed}')}\n"
    )
    if failed:
        summary += f"  {paint(RED, f'❌ провалено: {failed}')}\n"
    if skipped:
        summary += f"  {paint(YELLOW, f'➖ пропущено: {skipped}')}\n"
    summary += f"  всего:  {total}\n"

    print(summary)

    if failed:
        print(paint(RED, "⚠️  Некоторые тесты не прошли. Подробности выше."))
        return 1
    if skipped:
        print(paint(YELLOW, "ℹ️  Некоторые тесты пропущены (среда не готова)."))
    print(paint(GREEN, "✅ Все тесты пройдены."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())