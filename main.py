"""
main.py — единственная точка входа в SmartMusic.

Запуск:
    python main.py

Раньше приходилось писать путь руками, например:
    python src/ui/main_window.py
Это работало, но только если текущая папка совпадает с корнем проекта,
иначе ломался импорт config. Этот файл решает проблему: он всегда
настраивает пути к проекту, проверяет окружение и открывает окно.

При первом запуске библиотеки доставляются сами: main.py спрашивает у
setup.py, чего не хватает, и запускает его. Список зависимостей живёт
только в setup.py, здесь он не дублируется.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Корень проекта - папка, где лежит этот файл
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Импортируем config ДО первого print: он переключает консоль на UTF-8.
# Без этого русские буквы и эмодзи падают с UnicodeEncodeError
# на стандартной консоли Windows (там по умолчанию cp1251).
import config  # noqa: F401  - нужен ради побочного эффекта с кодировкой

from src import app_log

# Лог включается раньше любого print. Иначе первые строки - ровно те,
# ради которых лог и нужен, - остались бы только на экране.
_LOG_READY = app_log.setup_logging()


def _load_setup():
    """
    Загружает setup.py как модуль, чтобы забрать из него список зависимостей.

    Именно как файл, а не через import setup: модуль с таким именем может
    попасться в site-packages, и тогда проверяли бы чужой файл. Имя
    «smartmusic_setup» внутри ничего не значит - setup.py на импорт не
    смотрит, всё полезное у него под if __name__ == "__main__".
    """
    setup_path = ROOT_DIR / "setup.py"
    if not setup_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("smartmusic_setup", setup_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_dependencies() -> None:
    """
    Проверяет, что библиотеки на месте, и при необходимости доустанавливает.

    Если всё на месте - сразу выходит (return), ничего не печатая лишнего.
    Если чего-то нет - запускает setup.py и ждёт его.

    Отдельный процесс, а не импорт, потому что установку делает pip:
    ему нужен чистый вывод в свою консоль, нормальные Ctrl+C и право
    переустановить пакеты на лету. Внутри того же процесса всё это было бы
    неудобно, а сломанная установка забирала бы с собой и само приложение.
    """
    setup = _load_setup()
    if setup is None:
        print("⚠️ [Main] Не найден setup.py — проверка библиотек пропущена.")
        return None

    # Считаем один раз и переиспользуем: после установки список надо
    # проверить заново, и пересчитывать его три раза подряд незачем.
    absent_required = setup.missing(required=True)
    absent_all = setup.missing(required=False)
    if not absent_all:
        # Всё на месте. Молча идём дальше.
        return None

    required_set = set(absent_required)
    absent_optional = [entry for entry in absent_all if entry not in required_set]

    print("=" * 58)
    print("  Проверка библиотек")
    print("=" * 58)

    if absent_required:
        print("❌ Не хватает обязательных библиотек — без них не запуститься:")
        for pip_name, _module, purpose in absent_required:
            print(f"    • {pip_name:<32} {purpose}")
    if absent_optional:
        print("⚠️ Не хватает дополнительных (программа запустится, но часть "
              "функций работать не будет):")
        for pip_name, _module, purpose in absent_optional:
            print(f"    • {pip_name:<32} {purpose}")

    print("\nЗапускаю установку...\n")

    setup_path = ROOT_DIR / "setup.py"
    try:
        # sys.executable, а не "python": на Windows это гарантирует, что
        # ставим в тот же интерпретатор, который сейчас запущен, даже если
        # в PATH лежит несколько разных Python.
        code = subprocess.call([sys.executable, str(setup_path)])
    except KeyboardInterrupt:
        print("\n❌ Установка прервана.")
        sys.exit(1)

    # Две проверки, а не одна. pip иногда радостно отвечает «Successfully
    # installed», ничего не поставив (пакет несовместим с уже стоящим) -
    # тогда программа упала бы на импорте через миг. Проверяем факт,
    # а не обещание.
    if code != 0 or setup.missing(required=True):
        print("\n❌ [Main] Библиотеки не установились. Что делать:")
        print("   1) Запусти вручную и посмотри ошибку:  python setup.py")
        print("   2) Если конфликт версий — новое окружение:")
        print("      python -m venv .venv")
        print("      .venv\\Scripts\\activate")
        print("      pip install -r requirements.txt")
        sys.exit(1)

    print("\n✅ [Main] Библиотеки на месте, продолжаю.\n")


def _ollama_executable() -> str | None:
    """
    Ищет, чем запускать Ollama.

    Сначала PATH, потом типовые места установки на Windows. Нужен потому,
    что Ollama часто ставится в AppData и не попадает в PATH того окна,
    из которого запущена программа, - и тогда "ollama serve" из PowerShell
    работает, а из Python нет.
    """
    found = shutil.which("ollama")
    if found:
        return found

    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.append(Path(program_files) / "Ollama" / "ollama.exe")

    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _ollama_is_up() -> bool:
    """Отвечает ли демон. Дешёвая проверка: 4 секунды максимум."""
    try:
        from src.ai.llm_providers import get_provider
    except Exception:
        return False
    try:
        return bool(get_provider().available())
    except Exception:
        return False


def ensure_ollama() -> None:
    """
    Поднимает локальную нейросеть, если она не запущена.

    Уже работает - выходит сразу, ничего не печатая. Это тот же приём,
    что у ensure_dependencies: лишний вывод в консоль только мешает.

    Запускаем демон в отдельном окне, а не в фоне текущего процесса:
    демон живёт до выключения компьютера, а программа - до закрытия
    окна. Скрыто он работал бы, и выключить его было бы нечем, кроме
    диспетчера задач. Отдельное окно можно просто закрыть руками.

    Не запустилась - не беда и не повод закрывать программу. Без модели
    не сортируются только те треки, у которых не удалось скачать звук;
    на игру это не влияет.
    """
    if _ollama_is_up():
        return

    print("🧠 [Main] Нейросеть не отвечает, запускаю Ollama...")

    executable = _ollama_executable()
    if not executable:
        print("⚠️ [Main] Не нашёл, чем запустить Ollama.")
        print("   Установи её один раз:  https://ollama.com/download")
        print("   Пока не установлена, программа работает - без неё не")
        print("   сортируются только треки, у которых нет звука.")
        return

    try:
        # CREATE_NEW_CONSOLE - демон в своём окне, а не в этом.
        # На других системах флага нет, там просто запускаем как есть.
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen([executable, "serve"], creationflags=flags)
    except Exception as e:
        print(f"⚠️ [Main] Не смог запустить Ollama: {e}")
        print("   Запусти вручную в отдельном окне:  ollama serve")
        return

    # Демон поднимается не мгновенно: он читает список моделей и
    # поднимает сервер. Ждём, но недолго - иначе запуск программы
    # зависнет на минуту из-за того, что демон не нужен.
    for attempt in range(20):
        time.sleep(0.5)
        if _ollama_is_up():
            try:
                from src.ai.llm_providers import get_provider
                print(f"✅ [Main] Нейросеть поднялась: {get_provider().describe()}")
            except Exception:
                print("✅ [Main] Нейросеть поднялась.")
            return

    print("⚠️ [Main] Ollama запущена, но не отвечает.")
    print("   Возможно, модуль ещё грузится - это нормально при первом")
    print("   запуске. Проверить можно командой:  ollama list")
    print("   Программа работает и без неё.")


def main() -> int:
    app_log.write_header("SmartMusic")

    print("=" * 58)
    print("  SmartMusic — музыкальный ИИ-диджей для Dota 2")
    print("=" * 58)
    print(f"  Лог: {app_log.log_path()}")

    # Первым делом библиотеки: если чего-то нет, setup.py поставит.
    # Если всё на месте, проверка занимает миллисекунды и молчит.
    ensure_dependencies()

    # Нейросеть: если демон уже жив - выходим молча, если нет -
    # поднимаем его в отдельном окне.
    ensure_ollama()

    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        # Сюда попадаем только если ensure_dependencies отработал не так,
        # например setup.py лежит в другом месте. Сообщение должно говорить
        # не только «PyQt6 нет», но и что делать дальше.
        print("❌ [Main] PyQt6 всё-таки не импортируется.")
        print("   Выполни вручную:  python setup.py")
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
    try:
        code = main()
    except SystemExit:
        raise
    except Exception:
        # Последняя запись в лог перед падением. Обычный traceback
        # уходит в stderr, он тоже пишется в файл, но печать его
        # последней строкой сразу показывает, где именно упало.
        import traceback
        traceback.print_exc()
        print("\n❌ [Main] Программа упала. Подробности в логе.")
        code = 1
    finally:
        app_log.flush()
    sys.exit(code)
