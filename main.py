"""
main.py — единственная точка входа в SmartMusic.

Запуск:
    python main.py

Раньше приходилось писать путь руками, например:
    python src/ui/main_window.py
Это работало, но только если текущая папка совпадает с корнем проекта,
иначе ломался импорт config. Этот файл решает проблему: он всегда
настраивает пути к проекту, проверяет окружение и открывает окно.
"""

import sys
from pathlib import Path

# Корень проекта - папка, где лежит этот файл
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Импортируем config ДО первого print: он переключает консоль на UTF-8.
# Без этого русские буквы и эмодзи падают с UnicodeEncodeError
# на стандартной консоли Windows (там по умолчанию cp1251).
import config  # noqa: F401  - нужен ради побочного эффекта с кодировкой


def check_ollama() -> None:
    """
    Проверяет, отвечает ли локальная нейросеть, и предупреждает заранее.

    Проверка дешёвая (4 секунды максимум) и не падает, если демон выключен -
    просто печатает подсказку.
    """
    try:
        from src.ai.llm_providers import get_provider
    except Exception as e:
        print(f"⚠️ [Main] Не удалось загрузить модуль ИИ: {e}")
        return

    provider = get_provider()
    if not provider.available():
        print("⚠️ [Main] Локальная нейросеть (Ollama) не отвечает.")
        print("   Без неё сортировка треков не будет работать.")
        print("   Что сделать:")
        print("     1) Открой новое окно PowerShell и выполни:  ollama serve")
        print("     2) Скачай модель один раз:                     ollama pull qwen2.5:3b")
        return

    print(f"✅ [Main] ИИ готова: {provider.describe()}")


def main() -> int:
    print("=" * 58)
    print("  SmartMusic — музыкальный ИИ-диджей для Dota 2")
    print("=" * 58)

    check_ollama()

    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("❌ [Main] Не установлен PyQt6.")
        print("   Выполни:  pip install -r requirements.txt")
        return 1

    # Графический интерфейс требует, чтобы был создан QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("SmartMusic")

    from src.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    print("🚀 [Main] Окно открыто. Закрой окно, чтобы выйти.")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
