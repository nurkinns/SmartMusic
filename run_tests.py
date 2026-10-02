#!/usr/bin/env python3
"""
run_tests.py — запускает все автотесты SmartMusic.

Запуск:
    python run_tests.py

В консоли появятся названия тестов, справа от каждого — процент
выполнения на данный момент. Пройденные тесты зелёные, сломанные красные,
пропущенные (например, из-за выключенного демона Ollama) жёлтые.

Исходники проекта не трогаются: тесты сидят в tests/ и используют
заглушки вместо реального VLC.
"""

import sys
from pathlib import Path

# Корень проекта — туда же кладём этот файл.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.runner import main

if __name__ == "__main__":
    raise SystemExit(main())